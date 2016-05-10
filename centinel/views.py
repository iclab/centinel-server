from base64 import urlsafe_b64decode, urlsafe_b64encode
import config
from datetime import datetime
import flask
import GeoIP
import geoip2.errors
import geoip2.database
import glob
import hashlib
import json
import logging
from netaddr import IPNetwork
import os
import random
import re
import requests
import string
from werkzeug import secure_filename


# local imports
from centinel import constants
from centinel.models import Client, Role

import centinel
app = centinel.app
db = centinel.db
auth = centinel.auth

try:
    reader = geoip2.database.Reader(config.maxmind_db)
except (geoip2.database.maxminddb.InvalidDatabaseError, IOError):
    logging.warning("You appear to have an error in your geolocation "
                    "database. Your database is either corrupt or does not "
                    "exist until you download a new copy, geolocation "
                    "functionality will be disabled.")
    reader = None

try:
    logging.info("Loading AS info database...")
    asn_db_path = os.path.join(config.centinel_home, "asn-db.dat")
    as_lookup = GeoIP.open(asn_db_path, GeoIP.GEOIP_STANDARD)
    logging.info("Done loading AS info database.")
except Exception as exp:
    logging.warning(("Error loading ASN lookup information. You need a copy "
                     "of each ASN database file to enable this feature."))
    as_lookup = None


def normalize_ip(ip):
    """Take in an IP as a string in CIDR format or without subnet
    and normalize to a single IP for lookups

    """
    net = IPNetwork(ip)
    ip = str(net[0])
    return ip


def get_country_from_ip(ip):
    """Return the country for the given ip"""
    ip = normalize_ip(ip)
    try:
        return reader.country(ip).country.iso_code
    # if we have disabled geoip support, reader should be None, so the
    # exception should be triggered
    except (geoip2.errors.AddressNotFoundError,
            geoip2.errors.GeoIP2Error, AttributeError):
        return '--'


def get_asn_from_ip(ip, asn_reg=re.compile("AS(?P<asn>[0-9]+)")):
    """Get the owner and ASN for the IP"""
    ip = normalize_ip(ip)
    if as_lookup is None:
        return None, None
    owner = as_lookup.org_by_addr(ip)
    asn = None
    if owner is not None:
        asn = asn_reg.match(owner).group('asn')
    return asn, owner


def generate_typeable_handle(length=8):
    """Generate a random typeable (a-z, 1-9) string for consent URL."""
    return "".join([random.choice(string.digits +
                    string.ascii_lowercase) for _ in range(length)])


def update_client_info(username, ip, country=None):
    """Update client's information upon contact.
    This information includes their IP address,
    time when last seen, and country.

    Params:

    username-   username of the client who contaced.
    ip-         IP address of the client

    """
    client = Client.query.filter_by(username=username).first()
    if client is None:
        # this should never happen
        return
    # aggregate the ip to /24
    client.last_ip = ".".join(ip.split(".")[:3]) + ".0/24"
    client.last_seen = datetime.now()
    # if the client explicitely sets their country,
    # update the value based on that (used by VPN).
    if country is not None:
        client.country = country
    else:
        # don't update country for VPN clients unless
        # it was manually set.
        # this way we avoid changing country value when
        # uploading results without VPN connection.
        if not client.is_vpn:
            client.country = get_country_from_ip(ip)
    db.session.commit()


@app.errorhandler(404)
def not_found(error):
    return flask.make_response(flask.jsonify({'error': 'Not found'}), 404)


@app.errorhandler(400)
def bad_request(error):
    return flask.make_response(flask.jsonify({'error': 'Bad request'}), 400)


@app.errorhandler(418)
def no_consent(error):
    return flask.make_response(flask.jsonify({'error': 'Consent not given'}),
                               418)


@auth.error_handler
def unauthorized():
    json_resp = flask.jsonify({'error': 'Unauthorized access'})
    return flask.make_response(json_resp, 401)


@app.route("/version")
def get_recommended_version():
    return flask.jsonify({"version": config.recommended_version})


@app.route("/results", methods=['POST'])
@auth.login_required
def submit_result():
    update_client_info(flask.request.authorization.username,
                       flask.request.remote_addr)
    # abort if there is no result file
    if not flask.request.files:
        flask.abort(400)

    # make sure the informed consent has been given before we proceed
    username = flask.request.authorization.username
    client = Client.query.filter_by(username=username).first()
    if not client.has_given_consent:
        flask.abort(418)

    # TODO: overwrite file if exists?
    result_file = flask.request.files['result']
    client_dir = username

    # we assume that the directory was created when the user
    # registered
    file_name = secure_filename(result_file.filename)
    file_path = os.path.join(config.results_dir, client_dir, file_name)

    result_file.save(file_path)

    return flask.jsonify({"status": "success"}), 201


@app.route("/results")
@auth.login_required
def get_results():
    update_client_info(flask.request.authorization.username,
                       flask.request.remote_addr)
    results = {}

    # TODO: cache the list of results?
    # TODO: let the admin query any results file here?
    # look in results directory for the user's results (we assume this
    # directory was created when the user registered)
    username = flask.request.authorization.username
    user_dir = os.path.join(config.results_dir, username, '[!_]*.json')
    for path in glob.glob(user_dir):
        file_name, ext = os.path.splitext(os.path.basename(path))
        with open(path) as result_file:
            try:
                results[file_name] = json.load(result_file)
            except Exception, e:
                logging.error("Results: Couldn't open results file - %s - %s"
                              % (path, str(e)))

    return flask.jsonify({"results": results})


def get_user_specific_content(folder, filename=None, json_var=None):
    """Perform the functionality of get_experiments and get_inputs_files

    Params:

    filename- the name of the file to retrieve or None to fetch the
        hashes of all the files
    folder- the directory that the user's directory is contained in
    json_var- the name of the json variable to return containing the
    list of hashes

    """
    username = flask.request.authorization.username

    # make sure the informed consent has been given before we proceed
    client = Client.query.filter_by(username=username).first()
    if not client.has_given_consent:
        flask.abort(418)

    # all of the scheduler files are combined together here.
    # this is run every time the experiment list or "scheduler.info"
    # is requested.
    if (json_var == "experiments" and
       (filename is None or filename == "scheduler.info")):
        global_scheduler_filename = os.path.join(config.experiments_dir,
                                                 "global", "scheduler.info")
        country_scheduler_filename = os.path.join(config.experiments_dir,
                                                  client.country,
                                                  "scheduler.info")
        client_scheduler_filename = os.path.join(config.experiments_dir,
                                                 username, "scheduler.info")

        freqs = {}
        if os.path.exists(global_scheduler_filename):
            with open(global_scheduler_filename, 'r') as file_p:
                freqs.update(json.load(file_p))
        if os.path.exists(country_scheduler_filename):
            with open(country_scheduler_filename, 'r') as file_p:
                freqs.update(json.load(file_p))
        if os.path.exists(client_scheduler_filename):
            with open(client_scheduler_filename, 'r') as file_p:
                freqs.update(json.load(file_p))

    files = {}

    # include global baseline content
    global_dir = os.path.join(folder, "global")
    if os.path.exists(global_dir):
        for path in glob.glob(os.path.join(global_dir, "*")):
            file_name = os.path.basename(path)
            files[file_name] = path
    else:
        logging.warning("Global baseline folder \"%s\" "
                        "doesn't exist!" % (global_dir))

    # include country-specific baseline content
    country_specific_dir = os.path.join(folder, client.country)
    if os.path.exists(country_specific_dir):
        # if baseline experiments exist for this country (==folder exists),
        # sync up all of the files in that dir.
        for path in glob.glob(os.path.join(country_specific_dir, "*")):
            file_name = os.path.basename(path)
            files[file_name] = path
    else:
        logging.warning("Country baseline folder %s "
                        "doesn't exist!" % (country_specific_dir))

    user_dir = os.path.join(folder, username, '*')
    for path in glob.glob(user_dir):
        file_name = os.path.basename(path)
        files[file_name] = path

    if filename is None:
        for filename in files:
            with open(files[filename], 'r') as file_p:
                hash_val = hashlib.md5(file_p.read()).digest()
                files[filename] = urlsafe_b64encode(hash_val)

        return flask.jsonify({json_var: files})

    # this should never happen, but better be safe
    if '..' in filename or filename.startswith('/'):
        flask.abort(404)

    # we have to make a special case for scheduler.info
    # and send the composition of all 3 files as one file
    if filename == "scheduler.info":
        scheduler = json.dumps(freqs)
        response = flask.make_response(scheduler)
        response.headers["Content-Disposition"] = ("attachment; "
                                                   "filename=scheduler.info")
        return response

    if filename in files:
        # send requested experiment file
        return flask.send_file(files[filename])
    else:
        # not found
        flask.abort(404)


# in case the client wants to specify the country explicitly (VPN).
@app.route("/set_country/<country>")
@auth.login_required
def set_country(country):
    if country is None:
        flask.abort(404)

    try:
        update_client_info(flask.request.authorization.username,
                           flask.request.remote_addr, country)
    except Exception as exp:
        logging.error("Error setting country"
                      " %s: %s" % (country, exp))
        return flask.jsonify({"status": "failure"}), 400
    return flask.jsonify({"status": "success"}), 200


# in case the client wants to specify the IP address explicitly (VPN).
@app.route("/set_ip/<ip_address>")
@auth.login_required
def set_ip(ip_address):
    if ip_address is None:
        flask.abort(404)

    try:
        update_client_info(flask.request.authorization.username,
                           ip=ip_address)
    except Exception as exp:
        logging.error("Error setting IP address"
                      " %s: %s" % (ip_address, exp))
        return flask.jsonify({"status": "failure"}), 400
    return flask.jsonify({"status": "success"}), 200


@app.route("/experiments")
@app.route("/experiments/<name>")
@auth.login_required
def get_experiments(name=None):
    update_client_info(flask.request.authorization.username,
                       flask.request.remote_addr)
    return get_user_specific_content(config.experiments_dir, filename=name,
                                     json_var="experiments")


@app.route("/input_files")
@app.route("/input_files/<name>")
@auth.login_required
def get_inputs(name=None):
    update_client_info(flask.request.authorization.username,
                       flask.request.remote_addr)
    return get_user_specific_content(config.inputs_dir, filename=name,
                                     json_var="inputs")


@app.route("/clients")
def get_system_status():
    """This is a list of clients and the countries from which they last
    connected and when. This does not require authentication as it
    doesn't reveal anything important (e.g. IP address, username, etc.).
    The list is shuffled each time so that numbers are randomly assigned.

    Note: we don't display clients who have the dont_display column
    set to 1/True

    """
    clients = Client.query.all()
    random.shuffle(clients)
    results = []
    number = 0
    for client in clients:
        # dont add clients who have asked not to be displayed
        if client.dont_display:
            continue
        info = {}
        info['num'] = number
        info['country'] = client.country
        if client.last_seen is not None:
            info['last_seen'] = str(client.last_seen.date())
        else:
            continue
        info['is_vpn'] = client.is_vpn
        info['as_number'] = 0
        info['as_owner'] = ""
        try:
            asn, owner = get_asn_from_ip(client.last_ip)
            info['as_number'] = asn
            info['as_owner'] = owner.decode('utf-8', 'ignore')
        except Exception as exp:
            logging.error("Error looking up AS info for "
                          "%s: %s" % (client.last_ip, exp))
        results.append(info)
        number += 1
    return flask.jsonify({"clients": results})


@app.route("/client_details")
@auth.login_required
def get_clients():
    """This is a list of clients that is fully detailed.
    This requires both authentication and admin-level access.

    """
    update_client_info(flask.request.authorization.username,
                       flask.request.remote_addr)
    # ensure that the client has the admin role
    username = flask.request.authorization.username
    user = Client.query.filter_by(username=username).first()
    admin = Role.query.filter_by(name='admin').first()
    if user not in admin.users:
        return unauthorized()

    results = []
    clients = Client.query.all()
    for client in clients:
        info = {}
        info['username'] = client.username
        info['handle'] = client.typeable_handle
        info['country'] = client.country
        info['registered_date'] = client.registered_date
        info['last_seen'] = client.last_seen
        info['last_ip'] = client.last_ip
        info['is_vpn'] = client.is_vpn
        info['has_given_consent'] = client.has_given_consent
        info['date_given_consent'] = client.date_given_consent
        results.append(info)
    return flask.jsonify({"clients": results})


@app.route("/register", methods=["POST"])
def register():
    # TODO: use a captcha to prevent spam?
    if not flask.request.json:
        flask.abort(404)

    ip = flask.request.remote_addr

    # parse the info we need out of the json
    client_json = flask.request.get_json()
    username = client_json.get('username')
    password = client_json.get('password')
    # if the user didn't specify which country they were coming from,
    # pull it from geolocation on their ip
    country = client_json.get('country')
    if country is None or (len(country) != 2):
        client_json['country'] = get_country_from_ip(ip)
    client_json['ip'] = ip
    client_json['last_seen'] = datetime.now()
    client_json['registered_date'] = datetime.now()
    client_json['has_given_consent'] = False

    # a VPN client does not need to give consent
    if client_json.get('is_vpn'):
        client_json['is_vpn'] = True
        client_json['has_given_consent'] = True
        client_json['date_given_consent'] = datetime.now()
    else:
        client_json['is_vpn'] = False

    client_json['roles'] = ['client']

    if not username or not password:
        flask.abort(400)

    client = Client.query.filter_by(username=username).first()
    if client is not None:
        flask.abort(400)

    # create a typeable handle to put in the consent form URL
    typeable_handle = generate_typeable_handle(length=8)
    client = Client.query.filter_by(typeable_handle=typeable_handle).first()
    # if there is a collision, generate another one
    while client is not None:
        type_hand = generate_typeable_handle(length=8)
        client = Client.query.filter_by(typeable_handle=type_hand).first()
    client_json['typeable_handle'] = typeable_handle

    user = Client(**client_json)
    db.session.add(user)
    db.session.commit()

    os.makedirs(os.path.join(config.results_dir, username))
    os.makedirs(os.path.join(config.experiments_dir, username))
    os.makedirs(os.path.join(config.inputs_dir, username))

    ret_json = {"status": "success", "typeable_handle": typeable_handle}
    return flask.jsonify(ret_json), 201


@app.route("/meta/")
@app.route("/meta/<custom_ip>")
def geolocate(custom_ip=None):
    # this will return metadata about a client's IP
    # address and the current server time. this info
    # can be appended to experiment results.
    if custom_ip is not None:
        ip = custom_ip
    else:
        ip = flask.request.remote_addr

    results = {}
    ip_aggr = ip
    results['country'] = ''
    try:
        # aggregate ip to the /24
        ip_aggr = '.'.join(ip.split('.')[:3]) + '.0/24'
        country = get_country_from_ip(ip)
        results['country'] = country
    except Exception as exp:
        logging.error('Error looking up country for '
                      '%s: %s' % (ip, exp))
        results['country_error'] = str(exp)

    results['ip'] = ip_aggr
    results['as_number'] = 0
    results['as_owner'] = ''
    try:
        asn, owner = get_asn_from_ip(ip)
        results['as_number'] = asn
        results['as_owner'] = owner.decode('utf-8', 'ignore')
    except Exception as exp:
        logging.error('Error looking up AS info for '
                      '%s: %s' % (ip, exp))
        results['asn_error'] = str(exp)

    results['server_time'] = datetime.now().isoformat()

    return flask.jsonify(results)


def display_consent_page(username, path, freedom_url=''):
    # insert a hidden field into the form with the user's username
    with open(path, 'r') as file_p:
        initial_page = file_p.read()
    initial_page = initial_page.decode('utf-8')
    replace_field = u'replace-with-username-value'
    initial_page = initial_page.replace(replace_field,
                                        urlsafe_b64encode(username))
    replace_field = u'replace-with-human-readable-username-value'
    initial_page = initial_page.replace(replace_field, (username))
    if freedom_url != '':
        freedom_replacement = u'replace-this-with-freedom-house'
        initial_page = initial_page.replace(freedom_replacement,
                                            u'static/' + freedom_url)
    return initial_page

@app.route("/static/<filename>")
def static_resource(filename):
    file_path = os.path.join(config.centinel_home, 'static', filename)
    if os.path.isfile(os.path.join(file_path)) and (filename in config.static_files_allowed):
        return flask.send_from_directory(os.path.join(config.centinel_home, 'static'), filename)
    else:
        flask.abort(404)

@app.route("/consent/<typeable_handle>")
def get_initial_informed_consent_with_handle(typeable_handle):
    if typeable_handle is None:
        flask.abort(404)
    client = Client.query.filter_by(typeable_handle=typeable_handle).first()
    if client is None:
        flask.abort(404)
    if client.has_given_consent:
        return "Consent already given."
    username = client.username

    if config.prefetch_freedomhouse:
        page_path = os.path.join(config.centinel_home,'static','initial_informed_consent.html')
    else:
        page_path = os.path.join(config.centinel_home,'static','no_prefetch_informed_consent.html')

    return display_consent_page(username,
                                page_path)


@app.route("/get_initial_consent")
def get_initial_informed_consent():
    username = flask.request.args.get('username')
    if username is None:
        flask.abort(404)
    username = urlsafe_b64decode(str(username))
    client = Client.query.filter_by(username=username).first()
    if client is None:
        flask.abort(404)
    if client.has_given_consent:
        return "Consent already given."
    return display_consent_page(username,
                                os.path.join(config.centinel_home,'static','initial_informed_consent.html'))


@app.route("/get_informed_consent_for_country")
def get_country_specific_consent():
    username = flask.request.args.get('username')
    country = flask.request.args.get('country')
    if username is None or country is None:
        flask.abort(404)
    username = urlsafe_b64decode(str(username))
    client = Client.query.filter_by(username=username).first()
    if client is None:
        flask.abort(404)
    if client.has_given_consent:
        return "Consent already given."
    country = str(country).upper()
    if country not in constants.freedom_house_lookup:
        flask.abort(404)

    # if we don't already have the content from freedom house, fetch
    # it, then host it locally and insert it into the report
    freedom_url = "".join(["freedom_house_", country, ".html"])
    filename = os.path.join(config.centinel_home, "static", freedom_url)
    # get the content from freedom house if we don't already have it
    get_page_and_strip_bad_content(constants.freedom_house_url(country),
                                   filename)

    page_path = os.path.join(centinel_home,'static','informed_consent.html')
    page_content = display_consent_page(username, page_path, freedom_url)

    flask.url_for('static', filename=freedom_url)
    flask.url_for('static', filename='economistDemocracyIndex.pdf')
    flask.url_for('static', filename='consent.js')

    return page_content


def get_page_and_strip_bad_content(url, filename):
    """Get the given page, strip out all requests back to the original
    domain (identified via src tags), and write out the page

    Note: this will break stuff, but that is better than letting the
    domain know where and who our clients are

    Note: we expect the content to be fairly static, so we don't
    refetch it if we already have it

    """
    # if os.path.exists(filename):
    #     return
    req = requests.get(url)
    # replace external links with a blank reference (sucks for the
    # rendering engine to figure out, but hey, they get paid to work
    # that out)
    # also remove form tags and scripts
    sub_flags = re.MULTILINE | re.DOTALL
    replace_src = r'src\s*=\s*".*?"'
    page = re.sub(replace_src, "", req.content, flags=sub_flags)
    replace_href = r'href\s*=\s*".*?"'
    page = re.sub(replace_href, "", page, flags=sub_flags)
    replace_script = r'<\s*script.*?>.*?</\s*script\s*>'
    page = re.sub(replace_script, "", page, flags=sub_flags)
    replace_form = r'<\s*form.*?>.*?</\s*form\s*>'
    page = re.sub(replace_form, "", page, flags=sub_flags)
    with open(filename, 'w') as file_p:
        file_p.write(page)


@app.route("/submit_consent")
def update_informed_consent():
    username = flask.request.args.get('username')
    if username is None:
        flask.abort(404)
    username = urlsafe_b64decode(str(username))
    client = Client.query.filter_by(username=username).first()
    if client is None:
        flask.abort(404)
    if client.has_given_consent:
        return "Consent already given."
    client.has_given_consent = True
    client.date_given_consent = datetime.now().date()
    db.session.commit()
    response = ("Success! Thanks for registering; you are ready to start "
                "sending us censorship measurement results.")
    return response


@auth.verify_password
def verify_password(username, password):
    if (len(username) == 0) and (len(password) == 0):
        logging.warning(("Username and password are both empty. Are you sure "
                         "that you enabled the WSGI option for HTTP "
                         "authentication?\n"
                         "Add WSGIPassAuthorization On to your WSGI config "
                         "file under enabled-sites in Apache"))
    user = Client.query.filter_by(username=username).first()
    return user and user.verify_password(password)

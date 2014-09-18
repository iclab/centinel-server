import flask
import geoip2.errors
import geoip2.database
import glob
import json
import os

import config

from datetime import datetime
from werkzeug import secure_filename
from flask.ext.httpauth import HTTPBasicAuth
from flask.ext.sqlalchemy import SQLAlchemy
from passlib.apps import custom_app_context as pwd_context

app = flask.Flask("Centinel")
auth = HTTPBasicAuth()
db = SQLAlchemy(app)

try:
    reader = geoip2.database.Reader(config.maxmind_db)
except (geoip2.database.maxminddb.InvalidDatabaseError, IOError):
    print ("You appear to have an error in your geolocation database.\n"
           "Your database is either corrupt or does not exist\n"
           "until you download a new copy, geolocation functionality\n"
           "will be disabled")
    reader = None


def get_country_from_ip(ip):
    """Return the country for the given ip"""
    try:
        return reader.country(ip).country.iso_code
    # if we have disabled geoip support, reader should be None, so the
    # exception should be triggered
    except (geoip2.errors.AddressNotFoundError,
            geoip2.errors.GeoIP2Error, AttributeError):
        return '--'


# This is a table to create a mapping between users and their roles
# (permissions)
roles_tab = db.Table('roles_tab',
                 db.Column('user_id', db.Integer, db.ForeignKey('clients.id')),
                 db.Column('role_id', db.Integer, db.ForeignKey('role.id'))
)


class Client(db.Model):
    __tablename__ = 'clients'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(36), index=True)  # uuid length=36
    password_hash = db.Column(db.String(64))
    # there are at most 15 chars for ip plus 4 for netmask plus 1 for
    # space, so 20 total chars
    last_ip = db.Column(db.String(20))
    last_seen = db.Column(db.DateTime)
    registered_date = db.Column(db.DateTime)
    has_given_consent = db.Column(db.Boolean)
    date_given_consent = db.Column(db.DateTime)
    is_vpn = db.Column(db.Boolean)
    # we expect this to be a country code (2 chars)
    country = db.Column(db.String(2))

    # since a user can have multiple roles, we have a table to hold
    # the mapping between users and their roles
    roles = db.relationship('Role', secondary=roles_tab,
                            backref=db.backref('users', lazy='dynamic'))


    def __init__(self, username, password):
        self.username = username
        self.password_hash = pwd_context.encrypt(password)

    def verify_password(self, password):
        return pwd_context.verify(password, self.password_hash)


class Role(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(20))

    def __init__(self, name):
        self.name = name


@app.errorhandler(404)
def not_found(error):
    return flask.make_response(flask.jsonify({'error': 'Not found'}), 404)

@app.errorhandler(400)
def bad_request(error):
    return flask.make_response(flask.jsonify({'error': 'Bad request'}), 400)

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
    # abort if there is no result file
    if not flask.request.files:
        flask.abort(400)

    # TODO: overwrite file if exists?
    result_file = flask.request.files['result']
    client_dir = flask.request.authorization.username

    # we assume that the directory was created when the user
    # registered
    file_name = secure_filename(result_file.filename)
    file_path = os.path.join(config.results_dir, client_dir, file_name)

    result_file.save(file_path)

    return flask.jsonify({"status": "success"}), 201

@app.route("/results")
@auth.login_required
def get_results():
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
                print "Couldn't open file - %s - %s" % (path, str(e))

    return flask.jsonify({"results": results})

@app.route("/experiments")
@app.route("/experiments/<name>")
def get_experiments(name=None):
    experiments = {}

    # TODO: create an option to pull down all?
    # look in experiments directory for each user
    username = flask.request.authorization.username
    user_dir = os.path.join(config.experiments_dir, username, '[!_]*.py')
    for path in glob.glob(user_dir):
        file_name, _ = os.path.splitext(os.path.basename(path))
        experiments[file_name] = path

    # send all the experiment file names
    if name is None:
        return flask.jsonify({"experiments": experiments.keys()})

    # this should never happen, but better be safe
    if '..' in name or name.startswith('/'):
        flask.abort(404)

    if name in experiments:
        # send requested experiment file
        return flask.send_file(experiments[name])
    else:
        # not found
        flask.abort(404)

@app.route("/clients")
@auth.login_required
def get_clients():
    # TODO: ensure that only the admin can make this call
    clients = Client.query.all()
    return flask.jsonify(clients=[client.username for client in clients])


@app.route("/register", methods=["POST"])
def register():
    # TODO: use a captcha to prevent spam?
    if not flask.request.json:
        flask.abort(404)

    username = flask.request.json.get('username')
    password = flask.request.json.get('password')

    if not username or not password:
        flask.abort(400)

    client = Client.query.filter_by(username=username).first()

    if client is not None:
        flask.abort(400)

    user = Client(username=username, password=password)
    db.session.add(user)
    db.session.commit()

    os.makedirs(os.path.join(config.results_dir, username))
    os.makedirs(os.path.join(config.experiments_dir, username))

    return flask.jsonify({"status": "success"}), 201

@app.route("/geolocation")
def geolocate_client():
    # get the ip and aggregate to the /24
    ip = flask.request.remote_addr
    ip_aggr = ".".join(ip.split(".")[:3]) + ".0/24"
    country = get_country_from_ip(ip)
    return flask.jsonify({"ip": ip_aggr, "country": country})

@auth.verify_password
def verify_password(username, password):
    user = Client.query.filter_by(username=username).first()
    return user and user.verify_password(password)


if __name__ == "__main__":
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///%s' % (config.sqlite_db)
    if not os.path.exists(config.sqlite_db):
        sql_dir = os.path.dirname(config.sqlite_db)
        if not os.path.exists(sql_dir):
            os.makedirs(sql_dir)
        db.create_all()
    app.run(debug=True)

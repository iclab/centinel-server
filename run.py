import os
import sys
import argparse
import centinel
import centinel.models
import centinel.views
import config
import logging
from logging.handlers import RotatingFileHandler


if (2, 7, 9) > sys.version_info:
    print ("WARNING: Python is older than 2.7.9, "
           "using older SSL version. This is "
           "incompatible with Werkzeug 0.10.x and "
           "will break if you use Werkzeug >= 0.10.0")
    from OpenSSL import SSL
    py_279 = False
else:
    import ssl
    py_279 = True


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument('--adhoc', help='Use adhoc SSL key and certificate.',
                        action='store_true')
    return parser.parse_args()

if __name__ == "__main__":
    args = parse_args()
    # create the centinel directory if it doesn't exist
    if not os.path.exists(config.centinel_home):
        os.makedirs(config.centinel_home)
        print "Created centinel home directory at {}".format(config.centinel_home)

    db = centinel.db
    app = centinel.app

    # setup logging first
    log_handler = RotatingFileHandler(config.LOG_FILE)
    log_handler.setLevel(config.LOG_LEVEL)
    logger = logging.getLogger('werkzeug')
    logger.addHandler(log_handler)
    app.logger.addHandler(log_handler)

    db.create_all()

    Role = centinel.models.Role

    admin_role = db.session.query(Role).filter(Role.name == 'admin').all()
    if len(admin_role) == 0:
        db.session.add(Role('admin'))

    client_role = db.session.query(Role).filter(Role.name == 'client').all()
    if len(client_role) == 0:
        db.session.add(Role('client'))

    db.session.commit()
    if args.adhoc:
        context = 'adhoc'
    else:
        # default method should be TLS
        if py_279:
            context = ssl.SSLContext(ssl.PROTOCOL_TLSv1_2)
            context.load_cert_chain(config.ssl_cert, config.ssl_key)
        else:
            context = SSL.Context(SSL.TLSv1_METHOD)
            context.use_privatekey_file(config.ssl_key)
            context.use_certificate_file(config.ssl_cert)

        context.load_verify_locations(config.ssl_chain)

    # Also, I shouldn't have to say this, but *DO NOT COMMIT THE
    # KEY*. Under no circumstances should the key be committed
    app.run(host="0.0.0.0", port=8082, use_reloader=True,
            ssl_context=context, threaded=True)

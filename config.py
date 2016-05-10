import os
import getpass
import logging

# misc
recommended_version = 1.1
production = True

# user details
current_user  = getpass.getuser()
centinel_home = "/opt/centinel-server/"


# directory structure
results_dir     = os.path.join(centinel_home, 'results')
experiments_dir = os.path.join(centinel_home, 'experiments')
inputs_dir = os.path.join(centinel_home, 'inputs')
static_files_allowed = ['economistDemocracyIndex.pdf', 'consent.js']


# details for how to access the database
def load_uri_from_file(filename):
    with open(filename, 'r') as filep:
        uri = filep.read()
    return uri

# Setup the database to connect to
database_uri_file = os.path.join(centinel_home, "cent.pgpass")
if not production:
    DATABASE_URI = "postgresql://postgres:postgres@localhost/centinel"
else:
    DATABASE_URI = load_uri_from_file(database_uri_file)

maxmind_db = os.path.join(centinel_home, 'maxmind.mmdb')

# AS information lookup
net_to_asn_file   = os.path.join(centinel_home, 'data-raw-table')
asn_to_owner_file = os.path.join(centinel_home, 'data-used-autnums')

# consent form
prefetch_freedomhouse = False

# web server
ssl_cert  = "server.iclab.org.crt"
ssl_key   = "server.iclab.org.key"
ssl_chain = "server.iclab.org_bundle.crt"

LOG_FILE  = os.path.join(centinel_home, "centinel-server.log")
LOG_LEVEL = logging.DEBUG

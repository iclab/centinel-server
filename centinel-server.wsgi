activate_this = '/opt/centinel-server/env/bin/activate_this.py'
execfile(activate_this, dict(__file__=activate_this))

import sys
sys.stdout = sys.stderr

sys.path.insert(0, '/opt/centinel-server/code/')
sys.path.insert(0, '/home/local/lib/python2.7/dist-packages')
sys.path.insert(0, '/home/local/lib/python2.7/site-packages')

import centinel
import centinel.models
import centinel.views
import config

from centinel import app as application

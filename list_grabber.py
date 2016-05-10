#!/usr/bin/env python
#
# Abbas Razaghpanah (arazaghpanah@cs.stonybrook.edu)
# January 2015, Stony Brook University
#
# list_grabber.py: a script to hit a given URL and download
# all of the csv files linked on the index page.
# This can be run periodically to update URL lists for different 
# countries, censorship products, etc. using a URL repository.
# The script assumes HTTP basic authentication for the repository.
#
# For CSV files that are of form XX.csv, it is assumed that it
# belongs to a country XX and it will be copied to the related
# directory (which is created if it doesn't exist). Everything
# else will go to "global".


import argparse
import os
import re
import requests


from os import path
from requests.auth import HTTPDigestAuth
from urlparse import urljoin


def parse_args():
    parser = argparse.ArgumentParser()

    url_help = ('The URL for the index page. All of the file names from '
                'hyperlinks will be joined with this URL.')
    parser.add_argument('--url', '-U', help=url_help, required=True)

    user_help = ('Username and password for HTTP authentication. '
                 'It should be provided as username:password')
    parser.add_argument('--user', '-u', help=user_help, default=None)

    output_help = ('The output directory where the files are supposed to be '
                   'saved to. Defaults to current directory.')
    parser.add_argument('--output', '-o', help=output_help, default='.')

    digest_help = ('Enable HTTP digest authentication. If username and password '
                   'are provided, basic authentication is used as default.')
    parser.add_argument('--digest', '-d', help=digest_help, dest='digest', action='store_true')
    parser.set_defaults(digest=False)

    args = parser.parse_args()

    if not os.path.exists(args.output):
        parser.error("The output directory \"%s\" does not exist!" % args.output)

    if args.user is None and args.digest is False:
        parser.error('Digest authentication has been enabled but no username and password '
                     'given.')
    return args


if __name__ == "__main__":

    args = parse_args()
    url = args.url
    if args.user is not None:
        user, password = args.user.split(':')
        if args.digest:
            auth = HTTPDigestAuth(user, password)
        else:
            auth = (user, password)
    else:
        auth = None

    directory = os.path.join(args.output, "global")
    req = requests.get(url, auth=auth)
    print "Downloading list index."
    req.raise_for_status()
    csvs = re.findall('href=\"([^\'\.\"]+\.csv)\"', req.text)

    if not os.path.exists(directory):
        print "Creating \"global\" directory at %s." % directory
        os.makedirs(directory)

    for csvfile in csvs:
        path = urljoin(url, csvfile)
        print "Downloading  list \"%s\"." % path
        try:
            req = requests.get(path, auth=auth)
            req.raise_for_status()
        except Exception as exp:
            print "Error downloading file \"%s\": %s" % (path, exp)
            continue

        path = os.path.join(args.output, "global", csvfile)

        # find out if it is a country-specific list
        base = os.path.splitext(csvfile)[0].upper()
        if len(base) == 2:
            directory = os.path.join(args.output, base)
            if not os.path.exists(directory):
                print "Creating directory for country %s at %s." % (base, directory)
                os.makedirs(directory)
            path = os.path.join(directory, "country_list.csv")

        output = open(path, 'w')
        output.write(req.text.encode('utf-8'))
        output.close()

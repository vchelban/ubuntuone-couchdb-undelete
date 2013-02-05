#!/usr/bin/python
"""ubuntuone-couchdb-undelete.py: undelete couchdb.one.ubuntu.com documents"""

from optparse import OptionParser
from oauth import oauth

DBUS_BUS_NAME='com.ubuntu.sso'
DBUS_CREDENTIALS_PATH='/com/ubuntu/sso/credentials'
DBUS_CREDENTIALS_IFACE='com.ubuntu.sso.CredentialsManagement'
APP_NAME='Ubuntu One'

APPLICATION_ANNOTATIONS='application_annotations'
PRIVATE_APPLICATION_ANNOTATIONS='private_application_annotations'

import gobject, httplib2, simplejson, urlparse, cgi, urllib
from dbus.mainloop.glib import DBusGMainLoop
import dbus
DBusGMainLoop(set_as_default=True)

class OAuthHttpClient(object):

    def __init__(self):
        self.signature_method = oauth.OAuthSignatureMethod_HMAC_SHA1()
        self.consumer = None
        self.token = None
        self.client = httplib2.Http()

    def set_consumer(self, consumer_key, consumer_secret):
        self.consumer = oauth.OAuthConsumer(consumer_key,
                                            consumer_secret)

    def set_token(self, token, token_secret):
        self.token = oauth.OAuthToken( token, token_secret)

    def _get_oauth_request_header(self, url, method):
        """Get an oauth request header given the token and the url"""
        query = urlparse.urlparse(url).query

        oauth_request = oauth.OAuthRequest.from_consumer_and_token(
            http_url=url,
            http_method=method,
            oauth_consumer=self.consumer,
            token=self.token,
            parameters=dict(cgi.parse_qsl(query))
        )
        oauth_request.sign_request(oauth.OAuthSignatureMethod_HMAC_SHA1(),
                                   self.consumer, self.token)
        return oauth_request.to_header()

    def request(self, url, method="GET", body=None, headers={}):
        oauth_header = self._get_oauth_request_header(url, method)
        headers.update(oauth_header)
        return self.client.request(url, method, headers=headers, body=body)


class Application(object):

    def __init__(self):
        self.token = None
        self.consumer = None
        self.debug = False
        self.couchdb_host = None
        self.couchdb_dbpath = None
        self.client = OAuthHttpClient()

        # fixup parameters, for now we need to do extra work only for tomboy
        self.fixup_tomboy_revision = None

    def get_token_from_sso(self):
        try:
            bus = dbus.SessionBus()
            # Trying to access SSO service via dbus (Maverick, Natty)
            bus.start_service_by_name(DBUS_BUS_NAME)

            obj = bus.get_object(bus_name=DBUS_BUS_NAME,
                            object_path=DBUS_CREDENTIALS_PATH,
                            follow_name_owner_changes=True)
            proxy = dbus.Interface(object=obj,
                            dbus_interface=DBUS_CREDENTIALS_IFACE)
            info2 = {}
            info = proxy.find_credentials_sync(APP_NAME, info2)

            self.consumer = oauth.OAuthConsumer(info['consumer_key'],
                                                info['consumer_secret'])
            self.token = oauth.OAuthToken( info['token'],
                                           info['token_secret'])
            return True
        except:
            return False

    def get_token_from_gnomekeyring(self):
        try:
            import gnomekeyring
            consumer = oauth.OAuthConsumer("ubuntuone", "hammertime")
            items = []
            items = gnomekeyring.find_items_sync(
                gnomekeyring.ITEM_GENERIC_SECRET,
                {'ubuntuone-realm': "https://ubuntuone.com",
                'oauth-consumer-key': consumer.key})
            secret = items[0].secret

            self.consumer = consumer
            self.token = oauth.OAuthToken.from_string(secret)
            return True
        except:
            return False

    def run(self, url, options, doc_id=None):
        has_token = False
        for method in (self.get_token_from_sso,
                       self.get_token_from_gnomekeyring):
            has_token = method()
            if has_token:
                if self.debug:
                    print "Using token from %s" % method
                    print "Consumer: %s" % self.consumer.key
                    print "Consumer secret: %s" % self.consumer.secret
                    print "Token: %s" % self.token.key
                    print "Token secret: %s" % self.token.secret
                break
        if not has_token:
            print "ERROR: Access token cannot be found. I tried\n"\
                  "Ubuntu SSO and gnomekeyring and found nothing\n"\
                  "\n"\
                  "Please authorize your machine via Tomboy or Ubuntu One.\n"
            return

        self.client.set_consumer(self.consumer.key, self.consumer.secret)
        self.client.set_token(self.token.key, self.token.secret)

        self.main(url, options, doc_id)

    def is_deleted(self, document):
        is_deleted = False
        try:
            is_deleted = document[APPLICATION_ANNOTATIONS]\
                         ['Ubuntu One']\
                         [PRIVATE_APPLICATION_ANNOTATIONS]\
                         ['deleted']
        except:
            pass

        return is_deleted 

    def undelete(self, document):
        document[APPLICATION_ANNOTATIONS]\
                ['Ubuntu One']\
                [PRIVATE_APPLICATION_ANNOTATIONS]\
                ['deleted'] = False

        # basically we modify the document in place but we don't care.
        return document

    def document_generator(self, database):
        startkey = None
        has_more = True

        while has_more:
            url = self.base_url + '/_all_docs?include_docs=True' \
                                  '&limit=11&descending=false'
            if startkey is not None:
                url = url + '&startkey=%%22%s%%22' % startkey

            headers, content = self.client.request(url, "GET")

            if headers['status'] == '503':
                print "error %s" % headers['status']
                return

            response = simplejson.loads(content)
            rows = response['rows']

            if len(rows) == 0:
                return

            if len(rows) > 10:
                startkey = rows[10]['key']
                rows = rows[:10]
                has_more = True
            else:
                has_more = False

            for row in rows:
                yield row

    def run_collect_handler(self, database, document):
        if database == 'notes':
            if (document.has_key('content')):
               print str(document)
               lsr =  document['application_annotations']\
                           ['Tomboy']\
                           ['last-sync-revision']

               if self.fixup_tomboy_revision is None or \
                    self.fixup_tomboy_revision < lsr:
                  self.fixup_tomboy_revision = lsr

    def run_fixup_handler(self, database, document):
        if database == 'notes':
            assert self.fixup_tomboy_revision is not None

            self.fixup_tomboy_revision += 1

            document['application_annotations']\
                    ['Tomboy']\
                    ['last-sync-revision'] = self.fixup_tomboy_revision

        return document

    def friendly_title(self, database, document):
        title = document['_id']

        try:
            if database == 'notes':
                title = document['title']
            elif database == 'contacts':
                title = document['first_name'] + ' ' + document['last_name']
            elif database == 'bookmarks':
                title = document['uri']
        except:
            pass

        return title
                   
    def main(self, database, options, doc_id=None):
        # Cannot use views due to LP:731926

        host, dbpath = self.get_couchdb_info()
        dbpath = urllib.quote(dbpath + '/' + database, safe="")
        base_url = host + '/' + dbpath

        self.base_url = base_url

        docs = self.document_generator(database)

        docs_to_undelete = []

        for value in docs:
            doc = value['doc']

            if self.debug:
                print "Received %s" % (doc['_id'])

            if doc['_id'].startswith('_design/'):
                # we are not interested in views
                continue

            self.run_collect_handler(database, doc)

            if self.is_deleted(doc):
                print "Document '%s' (%s) was marked as deleted" % (
                        self.friendly_title(database, doc), doc['_id'])
                if (doc_id is not None \
                        and doc_id.lower() == doc['_id'].lower()) \
                        or doc_id is None:
                    print "Adding the document to undelete queue"
                    docs_to_undelete.append(doc)

        if len(docs_to_undelete) == 0:
            print "Nothing to undelete"
            if doc_id is not None:
                print "The document id you have specified was not found\n" \
                      "Please make sure your document id is correct"
            return

        print "Found %d deleted documents" % (len(docs_to_undelete), )

        if options.dry_run:
            print "Not doing anything since --dry-run is specified"
            return

        for doc in docs_to_undelete:

            doc = self.undelete(doc)
            doc = self.run_fixup_handler(database, doc)

            body = simplejson.dumps(doc)
            url = self.base_url + '/' + doc['_id']
            headers = { 'content-type': 'application/json' }

            stat, response = self.client.request(url, 'PUT',
                    body=body, headers=headers)
            if not stat['status'].startswith('2'):
                raise RuntimeError("Failed to update document: %s" % response)
            print "Updated %s" % (doc['_id'], )

        print "Done"

    def get_couchdb_info(self):
        if self.couchdb_host is not None:
            return self.couchdb_host, self.couchdb_dbpath

        infourl = "https://one.ubuntu.com/api/account/"
        resp, content = self.client.request(infourl, "GET")
        if resp['status'] == "200":
            document = simplejson.loads(content)
        else:
            raise ValueError("Error retrieving user data (%s) from %s" % (
                            resp['status'], infourl))

        self.couchdb_host = document['couchdb']['host']
        self.couchdb_dbpath = document['couchdb']['dbpath']

        return self.couchdb_host, self.couchdb_dbpath

if __name__ == "__main__":
    parser = OptionParser(usage="%prog [options] database [doc-id]")

    parser.add_option("--debug", dest="debug",
                      action="store_true", default=False,
                      help="Print additional debug information")

    parser.add_option("--dry-run", dest="dry_run",
                      action="store_true", default=False,
                      help="Do not modify the database")

    doc_id = None

    (options, args) = parser.parse_args()
    if len(args) == 2:
        doc_id = args[1]
    elif len(args) != 1:
        parser.error("You must specify a database")

    application = Application()
    if options.debug:
        application.debug = True
    application.run(args[0], options, doc_id)


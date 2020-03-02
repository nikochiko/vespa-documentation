#!/usr/bin/env python3

import os
import sys
import json
import yaml
import time
import urllib.request, urllib.parse, urllib.error
import subprocess


def find(json, path, separator = "."):
    if len(path) == 0: return json
    head, _, rest = path.partition(separator)
    return find(json[head], rest) if head in json else None


# extract <id> from form id:doc:doc::<id>
def get_document_id(id):
    return id[id.rfind(":")+1:]


def call(args):
    proc = subprocess.Popen(args, stdout=subprocess.PIPE)
    (out, err) = proc.communicate()
    return out


def get_token():
    token_file_path = "vespa_athens_token"
    if not os.path.isfile(token_file_path):
        response = json.loads(call([
            "curl",
            "-s",
            "--key", "/tokens/key",
            "--cert", "/tokens/cert",
            "https://zts.athens.yahoo.com:4443/zts/v1/domain/vespa.vespa/token",
            ]))
        with open(token_file_path, "w") as f:
            f.write(response["token"])
    with open(token_file_path, "r") as f:
        return f.read()


def get_private_key_path():
    private_key_path = "data-plane-private-key.pem"
    if not os.path.isfile(private_key_path):
        private_key_raw = os.environ['DATA_PLANE_PRIVATE_KEY']
        private_key = private_key_raw.replace(" ", "\n")
        with open(private_key_path, "w") as f:
            f.write("-----BEGIN PRIVATE KEY-----\n" + private_key  + "\n-----END PRIVATE KEY-----")
    return private_key_path

def get_public_cert_path():
    public_cert_path = "data-plane-public-key.pem"
    if not os.path.isfile(public_cert_path):
        public_key_raw = os.environ['DATA_PLANE_PUBLIC_KEY']
        public_key = public_key_raw.replace(" ", "\n")
        with open(public_cert_path, "w") as f:
            f.write("-----BEGIN CERTIFICATE-----\n" + public_key  + "\n-----END CERTIFICATE-----")
    return public_cert_path


def vespa_get(endpoint, operation, options):
    endpoint = endpoint[:-1] if endpoint.endswith("/") else endpoint
    url = "{0}/{1}?{2}".format(endpoint, operation, "&".join(options))
    print(url)
    return call([
        "curl",
        "-gsS",
        "--cert", get_public_cert_path(),
        "--key", get_private_key_path(),
        url ])


def vespa_delete(endpoint, operation, options):
    endpoint = endpoint[:-1] if endpoint.endswith("/") else endpoint
    url = "{0}/{1}?{2}".format(endpoint, operation, "&".join(options))
    return call([
        "curl",
        "-gsS",
        "--cert", get_public_cert_path(),
        "--key", get_private_key_path(),
        "-X", "DELETE",
        url
    ])


def vespa_post(endpoint, doc, docid):
    endpoint = endpoint[:-1] if endpoint.endswith("/") else endpoint
    url = "{0}/document/v1/doc/doc/docid/{1}".format(endpoint, docid)
    return call([
        "curl",
        "-sS",
        "-H", "Content-Type:application/json",
        "--cert", get_public_cert_path(),
        "--key", get_private_key_path(),
        "-X", "POST",
        "--data-binary", "{0}".format(doc),
        url
    ])


def vespa_visit(endpoint, continuation = None):
    options = []
    options.append("wantedDocumentCount=500")
    if continuation is not None and len(continuation) > 0:
        options.append("&continuation={0}".format(continuation))
    response = vespa_get(endpoint, "document/v1/doc/doc/docid", options)
    try:
        return json.loads(response)
    except:
        print("Unable to parse JSON response from {0}. Should not happen, endpoint down? response: {1}".format(endpoint, response))
        sys.exit(1)
    return {}


def vespa_remove(endpoint, doc_ids):
    options = []
    for doc_id in doc_ids:
        id = get_document_id(doc_id)
        vespa_delete(endpoint, "document/v1/doc/doc/docid/{0}".format(id), options)


def vespa_feed(endpoint, feed):
    for doc in get_docs(feed):
        document_id = find(doc, "fields.doctype") +  find(doc, "fields.path")
        print(vespa_post(endpoint, json.dumps(doc), document_id))

def get_docs(index):
    file = open(index, "r", encoding='utf-8')
    return json.load(file)

def get_indexed_docids(endpoint):
    docids = set()
    continuation = ""
    while continuation is not None:
        json = vespa_visit(endpoint, continuation)
        documents = find(json, "documents")
        if documents is not None:
            ids = [ find(document, "id") for document in documents ]
            for id in ids:
                print("Found {0}".format(id))
            docids.update(ids)
        continuation = find(json, "continuation")
    return docids


def get_feed_docids(feed):
    with open(feed, "r", encoding='utf-8') as f:
        feed_json = json.load(f)
    return set([ "id:doc:doc::" + find(doc, "fields.doctype") + find(doc, "fields.path") for doc in feed_json ])


def print_header(msg):
    print("")
    print("*" * 80)
    print("* {0}".format(msg))
    print("*" * 80)


def read_config():
    with open("_config.yml", "r") as f:
        return yaml.safe_load(f)


def update_endpoint(endpoint, config):
    do_remove_index = config["do_index_removal_before_feed"]
    do_feed = config["do_feed"]

    endpoint_url = endpoint["url"]
    endpoint_indexes = endpoint["indexes"]

    print_header("Retrieving already indexed document ids for endpoint {0}".format(endpoint_url))
    docids_in_index = get_indexed_docids(endpoint_url)
    print("{0} documents found.".format(len(docids_in_index)))

    if do_remove_index:
        print_header("Removing all indexed documents in {0}".format(endpoint_url))
        vespa_remove(endpoint_url, docids_in_index)
        print("{0} documents removed.".format(len(docids_in_index)))

    if do_feed:
        docids_in_feed = set()
        print_header("Parsing feed file(s) for document ids")
        for index in endpoint_indexes:
            assert os.path.exists(index)
            docids_in_feed = docids_in_feed.union(get_feed_docids(index))
        print("{0} documents found.".format(len(docids_in_feed)))

        if len(docids_in_feed) == 0:
            return

        docids_to_remove = docids_in_index.difference(docids_in_feed)
        if len(docids_to_remove) > 0:
            print_header("Removing indexed documents not in feed in {0}".format(endpoint_url))
            for id in docids_to_remove:
                print("Removing {0}".format(id))
            vespa_remove(endpoint_url, docids_to_remove)
            print("{0} documents removed.".format(len(docids_to_remove)))
        else:
            print("No documents to be removed.")

        for index in endpoint_indexes:
            print_header("Feeding {0} to {1}...".format(index, endpoint_url))
            print(vespa_feed(endpoint_url, index))

        print("{0} documents fed.".format(len(docids_in_feed)))


def main():
    config = read_config()
    for endpoint in config["endpoints"]:
        update_endpoint(endpoint, config)


if __name__ == "__main__":
    main()

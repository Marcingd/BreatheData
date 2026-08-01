# -*- coding: utf-8 -*-
"""Wysylka gotowych siatek do Cloudflare Workers KV."""
import json
import os

import requests

API = "https://api.cloudflare.com/client/v4"


class KV(object):
    def __init__(self, account=None, namespace=None, token=None):
        self.account = account or os.environ["CF_ACCOUNT_ID"]
        self.namespace = namespace or os.environ["CF_KV_NAMESPACE_ID"]
        self.token = token or os.environ["CF_API_TOKEN"]
        self.session = requests.Session()

    def _url(self, key):
        return "%s/accounts/%s/storage/kv/namespaces/%s/values/%s" % (
            API, self.account, self.namespace, key)

    def put(self, key, payload, content_type="application/octet-stream", ttl=172800):
        """Zapis pojedynczego klucza. TTL sprawia, ze stare godziny znikaja same."""
        r = self.session.put(
            self._url(key),
            headers={"Authorization": "Bearer %s" % self.token},
            params={"expiration_ttl": ttl},
            files={"value": ("value", payload, content_type)},
            timeout=120,
        )
        if r.status_code != 200:
            raise RuntimeError("KV PUT %s -> %s %s" % (key, r.status_code, r.text[:300]))
        return True

    def put_json(self, key, obj, ttl=172800):
        return self.put(key, json.dumps(obj).encode("utf-8"), "application/json", ttl)

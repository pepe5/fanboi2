import logging
import os
import transaction
import unittest
from pyramid import testing
from sqlalchemy import create_engine
from sqlalchemy.orm import Query
from webob.multidict import MultiDict
from fanboi2.models import DBSession, Base, redis_conn


logging.basicConfig()
logging.getLogger('sqlalchemy.engine').setLevel(logging.INFO)


DATABASE_URI = os.environ.get(
    'POSTGRESQL_TEST_DATABASE',
    'postgresql://fanboi2:@localhost:5432/fanboi2_test')


class DummyRedis(object):

    @classmethod
    def from_url(cls, *args, **kwargs):
        return cls()

    def __init__(self):
        self._store = {}
        self._expire = {}

    def get(self, key):
        return self._store.get(key)

    def set(self, key, value):
        try:
            value = bytes(value.encode('utf-8'))
        except AttributeError:
            pass
        self._store[key] = value

    def setnx(self, key, value):
        if not self.get(key):
            self.set(key, value)

    def exists(self, key):
        return key in self._store

    def expire(self, key, time):
        self._expire[key] = time

    def ttl(self, key):
        return self._expire.get(key, 0)

    def ping(self):
        return True


class _ModelInstanceSetup(object):

    def _newBoard(self, **kwargs):
        from fanboi2.models import Board
        return Board(**kwargs)

    def _newTopic(self, **kwargs):
        from fanboi2.models import Topic
        return Topic(**kwargs)

    def _newTopicMeta(self, **kwargs):
        from fanboi2.models import TopicMeta
        if not kwargs.get('post_count'):
            kwargs['post_count'] = 0
        return TopicMeta(**kwargs)

    def _newPost(self, **kwargs):
        from fanboi2.models import Post
        if not kwargs.get('ip_address'):
            kwargs['ip_address'] = '0.0.0.0'
        return Post(**kwargs)

    def _newPage(self, **kwargs):
        from fanboi2.models import Page
        return Page(**kwargs)

    def _newRule(self, **kwargs):
        from fanboi2.models import Rule
        return Rule(**kwargs)

    def _newRuleBan(self, **kwargs):
        from fanboi2.models import RuleBan
        return RuleBan(**kwargs)

    def _newRuleOverride(self, **kwargs):
        from fanboi2.models import RuleOverride
        return RuleOverride(**kwargs)

    def _makeBoard(self, **kwargs):
        board = self._newBoard(**kwargs)
        DBSession.add(board)
        DBSession.flush()
        return board

    def _makeTopic(self, **kwargs):
        topic = self._newTopic(**kwargs)
        DBSession.add(topic)
        DBSession.flush()
        return topic

    def _makeTopicMeta(self, **kwargs):
        topic_meta = self._newTopicMeta(**kwargs)
        DBSession.add(topic_meta)
        DBSession.flush()
        return topic_meta

    def _makePost(self, **kwargs):
        post = self._newPost(**kwargs)
        DBSession.add(post)
        DBSession.flush()
        return post

    def _makePage(self, **kwargs):
        page = self._newPage(**kwargs)
        DBSession.add(page)
        DBSession.flush()
        return page

    def _makeRule(self, **kwargs):
        rule = self._newRule(**kwargs)
        DBSession.add(rule)
        DBSession.flush()
        return rule

    def _makeRuleBan(self, **kwargs):
        rule_ban = self._newRuleBan(**kwargs)
        DBSession.add(rule_ban)
        DBSession.flush()
        return rule_ban

    def _makeRuleOverride(self, **kwargs):
        rule_override = self._newRuleOverride(**kwargs)
        DBSession.add(rule_override)
        DBSession.flush()
        return rule_override

class ModelMixin(_ModelInstanceSetup, unittest.TestCase):

    @classmethod
    def tearDownClass(cls):
        super(ModelMixin, cls).tearDownClass()
        Base.metadata.bind = None
        DBSession.remove()

    @classmethod
    def setUpClass(cls):
        super(ModelMixin, cls).setUpClass()
        engine = create_engine(DATABASE_URI)
        DBSession.configure(bind=engine)
        Base.metadata.bind = engine

    def setUp(self):
        super(ModelMixin, self).setUp()
        redis_conn._redis = DummyRedis()
        Base.metadata.drop_all()
        Base.metadata.create_all()
        transaction.begin()

    def tearDown(self):
        super(ModelMixin, self).tearDown()
        transaction.abort()
        Base.metadata.drop_all()
        redis_conn._redis = None

    def assertSAEqual(self, first, second, msg=None):
        if isinstance(first, Query):
            return self.assertListEqual(list(first), second, msg)
        else:
            return self.assertEqual(first, second, msg)


class RegistryMixin(unittest.TestCase):

    def tearDown(self):
        super(RegistryMixin, self).tearDown()
        testing.tearDown()

    def _makeConfig(self, request=None, registry=None):
        return testing.setUp(
            request=request,
            registry=registry)

    def _makeRequest(self, **kw):
        """:rtype: pyramid.request.Request"""
        request = testing.DummyRequest(**kw)
        request.user_agent = kw.get('user_agent', 'Mock/1.0')
        request.remote_addr = kw.get('remote_addr', '127.0.0.1')
        request.referrer = kw.get('referrer')
        request.content_type = 'application/x-www-form-urlencoded'
        request.params = MultiDict(kw.get('params') or {})
        return request

    def _makeRegistry(self, **kw):
        """:rtype: pyramid.registry.Registry"""
        from pyramid.registry import Registry
        registry = Registry()
        registry.settings = {
            'app.timezone': 'Asia/Bangkok',
            'app.secret': 'demo',
        }
        registry.settings.update(kw)
        return registry


class DummyAsyncResult(object):

    def __init__(self, id_, status, result=None):
        self._id = id_
        self._status = status
        self._result = result

    @property
    def id(self):
        return self._id

    @property
    def status(self):
        return self._status.upper()

    @property
    def state(self):
        from celery import states
        return getattr(states, self.status)

    def get(self):
        return self._result


class TaskMixin(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        from fanboi2.tasks import celery
        super(TaskMixin, cls).setUpClass()
        celery.config_from_object({'CELERY_ALWAYS_EAGER': True})

    @classmethod
    def tearDownClass(cls):
        from fanboi2.tasks import celery
        super(TaskMixin, cls).tearDownClass()
        celery.config_from_object({'CELERY_ALWAYS_EAGER': False})


class ViewMixin(ModelMixin, RegistryMixin, unittest.TestCase):

    def _make_csrf(self, request):
        import hmac
        import os
        from hashlib import sha1
        request.session['csrf'] = sha1(os.urandom(64)).hexdigest()
        request.params['csrf_token'] = hmac.new(
            bytes(request.registry.settings['app.secret'].encode('utf8')),
            bytes(request.session['csrf'].encode('utf8')),
            digestmod=sha1,
        ).hexdigest()
        return request

    def _POST(self, data=None):
        request = self._makeRequest(params=data)
        request.method = 'POST'
        return request

    def _GET(self, data=None):
        request = self._makeRequest(params=data)
        return request

    def _json_POST(self, data=None):
        request = self._makeRequest()
        request.content_type = 'application/json'
        request.json_body = data
        return request


class CacheMixin(unittest.TestCase):

    def _getRegion(self, store=None):
        from dogpile.cache import make_region
        return make_region().configure('dogpile.cache.memory', arguments={
            'cache_dict': store if store is not None else {}})

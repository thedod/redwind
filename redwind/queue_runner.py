import redis
import json
from . import queue


def queue_daemon(app, rv_ttl=500):
    while 1:
        msg = redis.blpop(app.config['REDIS_QUEUE_KEY'])
        blob = json.loads(msg[1])

        func_name = blob['func']
        key = blob['key']
        args = blob['args']
        kwargs = blob['kwargs']

        func = queue.function_name_map.get(func_name)

        try:
            with app.app_context():
                rv = func(*args, **kwargs)
        except Exception, e:
            rv = e
        if rv is not None:
            redis.set(key, json.dumps(rv))
            redis.expire(key, rv_ttl)
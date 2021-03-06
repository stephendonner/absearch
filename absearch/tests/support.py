import os
import subprocess
import sys
import signal
import time
from cStringIO import StringIO
from contextlib import contextmanager
import socket

import redis
from webtest import TestApp
from konfig import Config

from absearch import server
from absearch.aws import _get_connector, set_s3_file
from absearch.counters import RedisCohortCounters


def run_moto():
    socket.setdefaulttimeout(.1)
    args = [sys.executable, '-c',
            "from moto import server; server.main()",
            's3bucket_path']
    return subprocess.Popen(args, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE,
                            preexec_fn=os.setsid)


def run_redis():
    args = ['redis-server', '--port', '7777']
    return subprocess.Popen(args, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE,
                            preexec_fn=os.setsid)


_P = []
test_config = os.path.join(os.path.dirname(__file__), 'absearch.ini')
test_config_no_datadog = os.path.join(os.path.dirname(__file__),
                                      'absearch-nodatadog.ini')


def runServers():
    # run Moto & Redis
    _P.append(run_moto())
    _P.append(run_redis())

    time.sleep(.1)
    populate_S3()


def populate_S3():
    # populate the bucket in Moto
    config = Config(test_config)
    conn = _get_connector(config)
    conn.create_bucket(config['aws']['bucketname'])

    datadir = os.path.join(os.path.dirname(__file__), '..', '..', 'data')

    for file_ in (config['absearch']['config'],
                  config['absearch']['schema']):
        filename = os.path.join(datadir, file_)
        set_s3_file(filename, config)

    _redis = redis.StrictRedis(**dict(config['redis']))
    _redis.flushdb()


def flush_redis():
    config = Config(test_config)
    _redis = redis.StrictRedis(**dict(config['redis']))
    _redis.flushdb()


def dump_counters():
    config = Config(test_config)
    counters = RedisCohortCounters(**dict(config['redis']))
    return counters.dump()


def stopServers():
    for p in _P:
        try:
            os.killpg(p.pid, signal.SIGTERM)
            p.kill()
        except OSError:
            pass
        p.wait()

    _P[:] = []


def get_app(datadog=True):
    # create the web app
    server.app.debug = True
    if datadog:
        server.initialize_app(test_config)
    else:
        server.initialize_app(test_config_no_datadog)
    server.app.catchall = False
    return TestApp(server.app)


@contextmanager
def capture():
    oldout, olderr = sys.stdout, sys.stderr
    try:
        out = [StringIO(), StringIO()]
        sys.stdout, sys.stderr = out
        yield out
    finally:
        sys.stdout, sys.stderr = oldout, olderr
        out[0] = out[0].getvalue()
        out[1] = out[1].getvalue()

#!/usr/bin/python
# -*- coding: utf-8 -*-

# webutil.py: low level page request related methods, decorators, Flask app
#
# Author: Tomi.Mickelsson@iki.fi

import time
import peewee
import functools
from flask import Flask, request, session, g, redirect, abort, jsonify
from flask_session import Session
from flask.json import JSONEncoder

import db
import config
import datetime

import logging
log = logging.getLogger("webutil")


# create and configure the Flask app
app = Flask(__name__, static_folder=None)
app.config.update(config.flask_config)
Session(app)


# --------------------------------------------------------------------------
# API decorators

def login_required(func=None, role=None):
    """Decorator: must be logged on, and optionally must have the given role.
       Insert after app.route like this:
       @app.route('/api/users')
       @login_required(role='superuser')"""

    # yes, this is python magic, see https://blogs.it.ox.ac.uk/inapickle/2012/01/05/python-decorators-with-optional-arguments/
    if not func:
        return functools.partial(login_required, role=role)
    @functools.wraps(func)
    def inner(*args, **kwargs):
        return _check_user_role(role) or func(*args, **kwargs)
    return inner


def local_dev_only(func):
    """Decorator: this test method is available only in the local
    development machine."""

    @functools.wraps(func)
    def inner(*args, **kwargs):
        return func(*args, **kwargs) if config.IS_LOCAL_DEV else ''
    return inner


def _check_user_role(role):
    """Check that my role is atleast the given role. If not, log and return
    an error."""

    if not g.MYSELF or not g.MYSELF.is_role_atleast(role):
        err = "Unauthorized! {} {} user={}".format(
                request.method, request.path, g.MYSELF)
        return warn_reply(err, 401)


# --------------------------------------------------------------------------
# log error, get data about the request

def error_reply(errmsg, httpcode=400):
    """Logs an error and returns error code to the caller."""
    log.error(errmsg)
    return jsonify({"err":"{}: {}".format(httpcode, errmsg)}), httpcode

def warn_reply(errmsg, httpcode=400):
    """Logs a warning and returns error code to the caller."""
    log.warning(errmsg)
    return jsonify({"err":"{}: {}".format(httpcode, errmsg)}), httpcode

def get_agent():
    """Returns browser of caller."""
    return request.headers.get('User-Agent', '')

def get_ip():
    """Returns IP address of caller."""
    return request.headers.get('X-Real-IP') or request.remote_addr


# --------------------------------------------------------------------------
# before/after/error request handlers

@app.before_request
def before_request():
    """Executed always before a request. Connects to db, logs the request,
       prepares global data, loads current user."""

    # log request path+input, but not secrets
    params = request.json or request.args or request.form
    if params:
        cloned = None
        secret_keys = ["password", "passwd", "pwd"]
        for k in secret_keys:
            if k in params:
                if not cloned:
                    cloned = params.copy()
                cloned[k] = 'X'
        if cloned:
            params = cloned

    params = str(params or '')[:1000]
    method = request.method[:2]
    log.info("{} {} {}".format(method, request.path, params))

    # connect to db
    g.db = db.database
    g.db.connection()

    # have common data available in global g
    # but do not pollute g, store only the most relevant data
    g.HOST = request.headers.get('X-Real-Host', '')

    # load current user from db
    g.MYSELF = me = None
    if "userid" in session:
        try:
            g.MYSELF = me = db.get_user(session['userid'])
        except:
            # odd error, clear session!
            session.clear()
            return webutil.error_reply("unknown uid")

    g.ISLOGGED = me != None
    g.IS_SUPER_USER = me and me.role == "superuser"

    if me and me.role == "disabled":
        err = "account disabled"
        log.warn(err)
        return jsonify({"err":err}), 400

    # time the request
    g.t1 = time.time()

    # where did we link from? (but filter our internal links)
#     if request.referrer:
#         log.info("linked from "+request.referrer)


@app.after_request
def after_request(response):
    """Executed after a request, unless a request occurred."""

    # log about error
    logmethod = None
    if 400 <= response.status_code <= 599:
        logmethod = log.error
    elif not 200 <= response.status_code < 399:
        logmethod = log.warn
    if logmethod:
        logmethod("  {} {} {}".format(response.status_code,
            request.method, request.url))

    return response

@app.teardown_request
def teardown(error):
    """Always executed after a request."""

    if hasattr(g, "db"):
        g.db.close()

    # log warning when a request takes >1.0sec
    # (put long-running tasks into background)
    if hasattr(g, "t1"):
        delta = time.time()-g.t1
        if delta > 1.0:
            log.warn("SLOW! {} time={}".format(request.path, delta))


@app.errorhandler(404)
def page_not_found(error):
    err = "404: " + request.path
    return jsonify({"err":err}), 404


# --------------------------------------------------------------------------
# logging - is here because binds to session

class MyLogContextFilter(logging.Filter):
    """Injects contextual info, ip+userid, into the log."""

    def filter(self, record):
        if request:
            # take ip from a header or actual
            ip = get_ip()
            # take userid from the session
            uid = session.get("userid", "anon")
        else:
            ip = ""
            uid = "  -WORKER" # background worker

        record.ip = "local" if config.IS_LOCAL_DEV else ip
        record.uid = uid
        return True


def init_logging():
    """Initialize logging system."""

    prefix = "PROD " if config.IS_PRODUCTION else ""
    format = prefix+"%(levelname)3.3s %(uid)s@%(ip)s %(asctime)s %(filename)s %(message)s"
    dfmt = "%m%d%y-%H:%M:%S"
    logging.basicConfig(level=logging.INFO, format=format, datefmt=dfmt)

    # custom log data: userid + ip addr
    f = MyLogContextFilter()
    for handler in logging.root.handlers:
        handler.addFilter(f)


# --------------------------------------------------------------------------
# serializing models - REST JSON encoder

class MyJSONEncoder(JSONEncoder):
    def default(self, obj):
        if isinstance(obj, peewee.SelectQuery):
            return list(obj)
        if isinstance(obj, db.BaseModel):
            return obj.serialize()
        elif isinstance(obj, datetime.datetime):
#             dt_local = util.utc2local(obj)
            return obj.isoformat() if obj else None
        return JSONEncoder.default(self, obj)

app.json_encoder = MyJSONEncoder

init_logging()

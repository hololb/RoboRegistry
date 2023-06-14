"""
    RoboRegistry route wrappers
    @author: Lucas Bubner, 2023
"""
from flask import session, request, flash, redirect, abort, render_template, url_for
from db import get_uid_for, get_event, logged_out_data, get_user_data
from datetime import datetime
from functools import wraps
from pytz import timezone


def login_required(f):
    """
        Ensures all routes that require a user to be logged in are protected.
    """
    @wraps(f)
    def check(*args, **kwargs):
        if not session.get("token"):
            session["next"] = "/" + request.full_path.lstrip("/").rstrip("?")
            flash("You'll need to be signed in to continue. This won't take long!")
            return redirect("/")
        return f(*args, **kwargs)
    return check


def must_be_event_owner(f):
    """
        Ensure permissions for administrative actions are only performed by the owner.
    """
    @wraps(f)
    def check(event_id, *args, **kwargs):
        if not get_uid_for(event_id) == session.get("uid"):
            return abort(403)
        return f(event_id, *args, **kwargs)
    return check

def event_must_be_running(f):
    """
        Ensure an event is running to allow requests to be made.
    """
    @wraps(f)
    def check(event_id, *args, **kwargs):
        event = get_event(event_id)
        if not event:
            return f(event_id, *args, **kwargs)
        start_time = datetime.strptime(event["start_time"], "%H:%M").time()
        end_time = datetime.strptime(event["end_time"], "%H:%M").time()
        if not start_time <= datetime.now(timezone(event["timezone"])).time() <= end_time or not datetime.now(timezone(event["timezone"])).date() == event["date"]:
            return render_template("event/done.html.jinja", status="Failed: EVENT_NOT_RUNNING", message="We are unable to check you in as the event is not running. If the event has already concluded, it is no longer possible to check in. If the event is yet to start, we'll automatically enable check-ins when it is time.", event=event, user=logged_out_data)
        return f(event_id, *args, **kwargs)
    return check

def user_data_must_be_present(f):
    """
        Ensure a user's data is present to allow requests to be made.
    """
    @wraps(f)
    def check(*args, **kwargs):
        if not get_user_data():
            session["next"] = "/" + request.full_path.lstrip("/").rstrip("?")
            return redirect(url_for("auth.create_profile"))
        return f(*args, **kwargs)
    return check
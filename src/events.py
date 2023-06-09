"""
    Event creation and management functionality for RoboRegistry
    @author: Lucas Bubner, 2023
"""

import copy
import math
import os
import random
import re
from datetime import datetime
from string import ascii_letters, digits
from time import time
from urllib.parse import urlparse

from flask import Blueprint, render_template, request, session, redirect, abort, send_file, make_response
from flask_login import current_user, login_required
from pytz import all_timezones, timezone

import db
import qr
import utils
from wrappers import must_be_event_owner, event_must_be_running, user_data_must_be_present

events_bp = Blueprint("events", __name__, template_folder="templates")


@events_bp.route("/events/view")
@login_required
@user_data_must_be_present
def viewall():
    """
        View all personally affiliated events.
    """
    registered_events, created_events = db.get_my_events(getattr(current_user, "id"))
    done_events = {}
    deletions = []
    # Remove registered events that have already occurred, and add them to a seperate list
    for key, value in registered_events.items():
        if datetime.strptime(value["date"], "%Y-%m-%d") < datetime.now():
            deletions.append(key)
            done_events[key] = value
    for key in deletions:
        del registered_events[key]
    return render_template("dash/view.html.jinja", created_events=created_events, registered_events=registered_events,
                           done_events=done_events, user=getattr(current_user, "data"))


@events_bp.route("/events/view/<string:uid>")
@login_required
@user_data_must_be_present
def viewevent(uid: str):
    """
        View a specific user-owned event.
    """
    data = db.get_event(uid)
    if not data:
        abort(404)

    registered = owned = False
    for key, value in data["registered"].items():
        if value == "owner" and key == getattr(current_user, "id"):
            owned = True
            break
        elif key == getattr(current_user, "id"):
            registered = True
            break

    # Calculate time to event
    tz = timezone(data["timezone"])
    start_time = tz.localize(datetime.strptime(
        f"{data['date']} {data['start_time']}", "%Y-%m-%d %H:%M"))
    end_time = tz.localize(datetime.strptime(
        f"{data['date']} {data['end_time']}", "%Y-%m-%d %H:%M"))

    days, hours, minutes = utils.get_time_diff(datetime.now(tz), start_time)
    time_to_end = utils.get_time_diff(datetime.now(tz), end_time)

    time_msg = ""
    
    # Time to event start
    if days >= 7:
        time_msg += f"{days // 7} week(s) "
    if days % 7 > 0:
        time_msg += f"{days % 7} day(s) "
    if hours > 0:
        time_msg += f"{hours} hour(s) "
    if minutes > 0:
        time_msg += f"{minutes} minute(s) "

    # Time to event end
    if days < 0 and time_to_end[0] >= 0:
        if time_to_end[1] > 0:
            time_msg = f"Ends in {time_to_end[1]} hours {time_to_end[2]} minute(s)"
        else:
            time_msg = f"Ends in {time_to_end[2]} minute(s)"
    elif days < 0:
        time_msg = "Event has ended."

    can_register = time_msg.startswith("Ends") or time_msg.startswith("Event")
    tz = timezone(data["timezone"])
    offset = tz.utcoffset(datetime.now()).total_seconds() / 3600

    if not data:
        abort(409)

    return render_template("event/event.html.jinja", user=getattr(current_user, "data"), event=data,
                           registered=registered, owned=owned, mapbox_api_key=os.getenv("MAPBOX_API_KEY"),
                           time=time_msg,
                           can_register=not can_register, timezone=tz, offset=offset)


@events_bp.route("/events", methods=["GET", "POST"])
@login_required
@user_data_must_be_present
def redirector():
    """
        Manage redirector for /events
    """
    if request.method == "POST":
        if not (target := request.form.get("event_url")):
            return render_template("dash/redirector.html.jinja", user=getattr(current_user, "data"),
                                   error="Missing url!")
        if not (event := db.get_event(target)):
            res = make_response(redirect(target))
            # Test to see if it is a url and/or if it is a 404
            target = urlparse(target).hostname
            if target and target not in ["roboregistry.vercel.app", "rbreg.vercel.app"] or res.status_code == 404:
                return render_template("dash/redirector.html.jinja", user=getattr(current_user, "data"),
                                       error="Hmm, we can't seem to find that event.")
            return res
        return redirect(f"/events/view/{event['uid']}")
    else:
        return render_template("dash/redirector.html.jinja", user=getattr(current_user, "data"))


@events_bp.route("/events/create", methods=["GET", "POST"])
@login_required
@user_data_must_be_present
def create():
    """
        Create a new event on the system.
    """
    user = getattr(current_user, "data")
    mapbox_api_key = os.getenv("MAPBOX_API_KEY")
    if request.method == "POST":
        if not (name := request.form.get("event_name")):
            return render_template("event/create.html.jinja", error="Please enter an event name.", user=user,
                                   mapbox_api_key=mapbox_api_key)
        if not (date := request.form.get("event_date")):
            return render_template("event/create.html.jinja", error="Please enter an event date.", user=user,
                                   mapbox_api_key=mapbox_api_key)

        if len(name) > 32:
            return render_template("event/create.html.jinja", error="Event name can only be 32 characters.", user=user,
                                   mapbox_api_key=mapbox_api_key)

        # Sanitise the name by removing everything that isn't alphanumeric and space
        # If the string is completely empty, return an error
        name = re.sub(r'[^a-zA-Z0-9 ]+', '', name)
        if not name:
            return render_template("event/create.html.jinja", error="Please enter a valid event name.", user=user,
                                   mapbox_api_key=mapbox_api_key)

        # Generate an event UID
        event_uid = re.sub(r'[^a-zA-Z0-9]+', '-', name.lower()
                           ) + "-" + date.replace("-", "")

        # Create the event and generate a UID
        event = {
            "name": name,
            # UIDs are in the form of <event name seperated by dashes><date seperated by dashes>
            "creator": getattr(current_user, "id"),
            "date": date,
            "start_time": request.form.get("event_start_time"),
            "end_time": request.form.get("event_end_time"),
            "description": request.form.get("event_description"),
            "email": request.form.get("event_email") or user["email"],
            "location": request.form.get("event_location"),
            "limit": utils.limit_to_999(request.form.get("event_limit")),
            "timezone": request.form.get("event_timezone"),
            "registered": {getattr(current_user, "id"): "owner"},
            "checkin_code": random.randint(1000, 9999)
        }

        if db.get_event(event_uid):
            return render_template("event/create.html.jinja", error="An event with that name and date already exists.",
                                   user=user, mapbox_api_key=mapbox_api_key, old_data=event, timezones=all_timezones)

        if not event["limit"]:
            event["limit"] = -1

        # Make sure all fields were filled
        if not all(event.values()):
            return render_template("event/create.html.jinja", error="Please fill out all fields.", user=user,
                                   mapbox_api_key=mapbox_api_key, timezones=all_timezones, old_data=event)

        # Make sure start time is before end time
        if datetime.strptime(event["start_time"], "%H:%M") > datetime.strptime(event["end_time"], "%H:%M"):
            return render_template("event/create.html.jinja", error="Please enter a start time before the end time.",
                                   user=user, mapbox_api_key=mapbox_api_key, timezones=all_timezones, old_data=event)

        tz = timezone(event["timezone"])
        event_start_time = tz.localize(datetime.strptime(
            event["date"] + event["start_time"], "%Y-%m-%d%H:%M"))
        if event_start_time < datetime.now(tz):
            return render_template("event/create.html.jinja", error="Please enter a date and time in the future.",
                                   user=user, mapbox_api_key=mapbox_api_key, timezones=all_timezones, old_data=event)

        db.add_event(event_uid, event)
        return redirect(f"/events/view/{event_uid}")
    else:
        return render_template("event/create.html.jinja", user=user, mapbox_api_key=mapbox_api_key,
                               timezones=all_timezones, old_data={})


@events_bp.route("/events/delete/<string:event_id>", methods=["GET", "POST"])
@login_required
@must_be_event_owner
@user_data_must_be_present
def delete(event_id: str):
    """
        Delete an event from the system.
    """
    if request.method == "POST":
        db.delete_event(getattr(current_user, "uid", None), event_id)
        return redirect("/events/view")
    else:
        return render_template("event/delete.html.jinja", event=db.get_event(event_id),
                               user=getattr(current_user, "data"))


@events_bp.route("/events/register/<string:event_id>", methods=["GET", "POST"])
@login_required
@user_data_must_be_present
def event_register(event_id: str):
    """
        Register a user for an event.
    """
    event = db.get_event(event_id)
    unmodified_event = copy.deepcopy(event)
    user = getattr(current_user, "data")
    # Check to see if the event is over, and decline registration if it is
    tz = timezone(event["timezone"])
    tz.localize(datetime.strptime(
        event["date"] + event["start_time"], "%Y-%m-%d%H:%M"))
    if datetime.now(tz) > tz.localize(datetime.strptime(event["date"] + event["end_time"], "%Y-%m-%d%H:%M")):
        return render_template("event/done.html.jinja", event=event, user=user, status="Failed: EVENT_AUTO_CLOSED",
                               message="This event has already ended. Registation has been automatically disabled.")

    if request.method == "POST":
        # Store registered users data
        try:
            prev_reg = event["registered"]
        except KeyError:
            prev_reg = {}

        try:
            prev_data = event["registered_data"]
        except KeyError:
            prev_data = {}

        event |= {
            "registered_data": {
                **prev_data,
                getattr(current_user, "id"): {
                    "teamName": request.form.get("teamName"),
                    "role": request.form.get("role"),
                    "teamNumber": request.form.get("teamNumber") or "N/A",
                    "numPeople": request.form.get("numPeople"),
                    "numStudents": utils.limit_to_999(request.form.get("numStudents")),
                    "numMentors": utils.limit_to_999(request.form.get("numMentors")),
                    "numAdults": utils.limit_to_999(request.form.get("numAdults")) or "0",
                    "contactName": request.form.get("contactName") or f"{user['first_name']} {user['last_name']}",
                    "contactEmail": user["email"],
                    "contactPhone": request.form.get("contactPhone") or "N/A",
                }
            },
            "registered": {
                **prev_reg,
                getattr(current_user, "id"): "pending"
            }
        }
        # Ensure that all required fields are filled
        if not all(event["registered_data"][getattr(current_user, "id")].values()):
            return render_template("event/done.html.jinja", event=event, status="Failed: MISSING_FIELDS",
                                   message="Please fill out all required fields.", user=user)

        # Check if the teamName is already taken
        for data in event["registered_data"].values():
            try:
                if data["teamName"] == unmodified_event["registered_data"][getattr(current_user, "id")]["teamName"]:
                    return render_template("event/done.html.jinja", event=event, status="Failed: TEAMNAME_TAKEN",
                                           message="This team name is already taken. Please choose another.", user=user)
            except KeyError:
                # Will throw an error if un_event["registered_data"] does not exist. We can ignore this as we can't use .get()
                pass

        # Check for event capacity
        if event["limit"] != -1 and len(unmodified_event["registered"]) - 1 >= event["limit"]:
            event["registered"][getattr(current_user, "id")] = "excess-" + str(math.floor(time()))
            db.update_event(event_id, event)
            return render_template("event/done.html.jinja", event=event, status="Failed: EVENT_FULL",
                                   message="This event has reached it's maximum capacity. Your registration has been placed on a waitlist, and we'll automatically add you if a spot frees up.",
                                   user=user)

        # Log event registration
        event["registered"][getattr(current_user, "id")] = math.floor(time())
        db.update_event(event_id, event)
        return render_template("event/done.html.jinja", event=event, status="Registration successful",
                               message="Your registration was successfully recorded. Go to the dashboard to view all your registered events, and remember to bring a smart device for QR code check-in on the day.",
                               user=user)
    else:
        if getattr(current_user, "id") in event["registered"]:
            if event["registered"][getattr(current_user, "id")] == "owner":
                return render_template("event/done.html.jinja", event=event, status="Failed: REGIS_OWNER",
                                       message="The currently logged in RoboRegistry account is the owner of this event. The owner cannot register for their own event.",
                                       user=getattr(current_user, "data"))
            return render_template("event/done.html.jinja", event=event, status="Failed: REGIS_ALR",
                                   message="You are already registered for this event. If you wish to unregister from this event, please go to the event view tab and unregister from there.",
                                   user=user)
        return render_template("event/register.html.jinja", event=event, user=user,
                               mapbox_api_key=os.getenv("MAPBOX_API_KEY"))


@events_bp.route("/events/unregister/<string:event_id>", methods=["GET", "POST"])
@login_required
@user_data_must_be_present
def event_unregister(event_id: str):
    if request.method == "POST":
        event = db.get_event(event_id)
        if event["creator"] == getattr(current_user, "id"):
            return render_template("event/done.html.jinja", event=event, status="Failed: REGIS_OWNER",
                                   message="The currently logged in RoboRegistry account is the owner of this event. The owner cannot unregister from their own event.",
                                   user=getattr(current_user, "data"))
        if getattr(current_user, "id") in event["registered"]:
            del event["registered"][getattr(current_user, "id")]
            del event["registered_data"][getattr(current_user, "id")]
            db.update_event(event_id, event)
            db.refresh_excess(event_id)
            return render_template("event/done.html.jinja", event=event, status="Unregistration successful",
                                   message="Your registration was successfully removed.",
                                   user=getattr(current_user, "data"))
        else:
            return render_template("event/done.html.jinja", event=event, status="Failed: REGIS_NF",
                                   message="You are not registered for this event.", user=getattr(current_user, "data"))
    else:
        return render_template("event/unregister.html.jinja", event=db.get_event(event_id),
                               user=getattr(current_user, "data"))


@events_bp.route("/events/gen/<string:event_id>", methods=["GET", "POST"])
@login_required
@must_be_event_owner
@user_data_must_be_present
def gen(event_id: str):
    """
        Generate a QR code for an event.
    """
    event = db.get_event(event_id)
    # Check if the event is done, reject if it is
    tz = timezone(event["timezone"])
    tz.localize(datetime.strptime(
        event["date"] + event["start_time"], "%Y-%m-%d%H:%M"))
    if tz.localize(datetime.strptime(event["date"] + event["end_time"], "%Y-%m-%d%H:%M")) < datetime.now(tz):
        return render_template("event/done.html.jinja", event=event, status="Failed: QR_GEN_FAIL",
                               message="Unable to generate QR codes for an event that has ended, as registration and check-in links are no longer active.",
                               user=getattr(current_user, "data"))
    if request.method == "POST":
        # Interpret data
        size = request.form.get("size")
        qr_type = request.form.get("type")
        if not size or not qr_type:
            return render_template("event/gen.html.jinja", error="Please fill out all fields.", event=event,
                                   user=getattr(current_user, "data"))
        # Generate QR code based on input
        qrcode = qr.generate_qrcode(event, size, qr_type)
        if not qrcode:
            return render_template("event/gen.html.jinja", error="An error occurred while generating the QR code.",
                                   event=event, user=getattr(current_user, "data"))
        # Send file to user
        return send_file(qrcode, mimetype="image/png")
    else:
        return render_template("event/gen.html.jinja", event=event, user=getattr(current_user, "data"))


@events_bp.route("/events/ci/<string:event_id>", methods=["GET", "POST"])
@event_must_be_running
def checkin(event_id: str):
    """
        Check in to an event.
    """
    event = db.get_event(event_id)
    if request.method == "POST":
        # Validate checkin code
        code = request.form.get("code")
        if not code or code != str(event["checkin_code"]):
            return render_template("event/done.html.jinja", event=event, status="Failed: CI_INVALID",
                                   message="You have provided an invalid check-in code! If trouble persists, try registration email check-in or asking the event owner to record you as attended manually.",
                                   user=getattr(current_user, "data") or db.logged_out_data)
        session["checkin"] = event["checkin_code"]
        # Redirect to event check-in page with approval
        return redirect("/events/ci/" + event_id + "/dynamic")
    else:
        return render_template("event/gatekeep.html.jinja", event=event)


@events_bp.route("/events/ci/<string:event_id>/dynamic", methods=["GET", "POST"])
@event_must_be_running
def dynamic(event_id: str):
    """
        Check into an event using check in code approval.
    """
    event = db.get_event(event_id)
    code = session.get("checkin")
    if not code or code != event.get("checkin_code") or not event:
        # Send them back to the check-in page if they don't have a valid check-in code
        return redirect("/events/ci/" + event_id)

    registered = []
    if event.get("registered_data"):
        for registration in event["registered_data"].values():
            if not registration.get("checkin"):
                registered.append(
                    f"{registration['teamName'].upper()}, behalf of {registration['contactName']}")
    if request.method == "POST":
        # Get the entity of which we are checking in
        entity = request.form.get("entity")
        anon_affil = request.form.get("visit-reason")
        anon_name = request.form.get("anon-name")
        if not entity or (entity == "anon" and not anon_affil and not anon_name):
            return render_template("event/done.html.jinja", event=event, status="Failed: CI_INVALID",
                                   message="You have provided insufficient data! If trouble persists, try registration email check-in or asking the event owner to record you as attended manually.",
                                   user=getattr(current_user, "data") or db.logged_out_data)
        # Put check-in data into the event data
        if not anon_affil:
            try:
                prev = event["registered_data"][db.get_uid_for_entity(
                    event_id, entity)]
            except KeyError:
                prev = {}
            event |= {
                "registered_data": {
                    db.get_uid_for_entity(event_id, entity): {
                        **prev,
                        "checkin": math.floor(time()),
                    }
                }
            }
        else:
            try:
                # Attempt to increment the number of times this entity has checked in
                affil_num = event["registered_data"]["anon_data"][anon_affil] + 1
            except KeyError:
                affil_num = 1
            try:
                prev = event["registered_data"]["anon_data"]["times"]
            except KeyError:
                prev = {}
            try:
                prev_data = event["registered_data"]
            except KeyError:
                prev_data = {}
            event |= {
                "registered_data": {
                    **prev_data,
                    "anon_data": {
                        anon_affil: affil_num,
                        "times": {
                            **prev,
                            "".join(random.choice(ascii_letters + digits + "_-") for _ in range(16)): {
                                "entity": anon_affil,
                                "name": anon_name,
                                "checkin": math.floor(time())
                            }
                        }
                    }
                }
            }
        db.update_event(event_id, event)
        return render_template("event/done.html.jinja", event=event, status="Check in successful",
                               message="Your check in has been recorded successfully by dynamic self check-in. You can safely close this tab.",
                               user=getattr(current_user, "data") or db.logged_out_data)
    else:
        return render_template("event/checkin.html.jinja", event=event, registered=registered)


@events_bp.route("/events/ci/<string:event_id>/manual", methods=["GET", "POST"])
@event_must_be_running
@login_required
@user_data_must_be_present
def manual(event_id: str):
    """
        Check in to an event using email associated with registration.
    """
    event = db.get_event(event_id)
    registered, owned = db.get_my_events(getattr(current_user, "id", None))
    if event_id in owned.keys():
        return render_template("event/done.html.jinja", event=event, status="Failed: EVENT_OWNER",
                               message="The currently logged in RoboRegistry account is the owner of this event. The owner cannot check in to their own event.",
                               user=getattr(current_user, "data"))
    if event_id not in registered.keys():
        return render_template("event/done.html.jinja", event=event, status="Failed: NO_AFFIL",
                               message="The currently logged in RoboRegistry account has not registered for this event. The event owner will have to manually record your presence through their Dashboard.",
                               user=getattr(current_user, "data"))
    if request.method == "POST":
        # Attempt a manual registration using the affiliated email
        event |= {
            "checked_in": {
                session['uid']: str(math.floor(time()))
            }
        }
        db.update_event(event_id, event)
        return render_template("event/done.html.jinja", event=event, status="Check in successful",
                               message="Your check in has been recorded successfully by registered email verification. You can safely close this tab.",
                               user=getattr(current_user, "data"))
    else:
        return render_template("event/manual.html.jinja", event=event, user=getattr(current_user, "data"),
                               data=registered[event_id]["registered_data"][getattr(current_user, "id")])


@events_bp.route("/events/ci", methods=["GET", "POST"])
def ci():
    """
        Check-in redirector.
    """
    if request.method == "POST":
        # Redirect to the link found in the QR code
        link = request.form.get("event_url")
        target = urlparse(link).hostname
        if not link or (target and target not in ["roboregistry.vercel.app", "rbreg.vercel.app"]):
            return render_template("event/qr.html.jinja")
        return redirect(link)
    else:
        return render_template("event/qr.html.jinja")


@events_bp.route("/events/manage/<string:event_id>", methods=["GET", "POST"])
@login_required
@user_data_must_be_present
def manage(event_id: str):
    """
        Manage and view an event's data.
    """
    event = db.get_event(event_id)
    return render_template("event/manage.html.jinja", event=event, user=getattr(current_user, "data"))

import os
import time
import smtplib
import logging
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from dotenv import load_dotenv
from pymongo import MongoClient
from bson import ObjectId

#For logs
load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

#Environment variables
MONGODB_URI = os.getenv("MONGODB_URI")
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL_SECONDS", "5"))
SMTP_HOST = os.getenv("SMTP_HOST", "localhost")
SMTP_PORT = int(os.getenv("SMTP_PORT", "1025"))
EMAIL_FROM = os.getenv("EMAIL_FROM", "worker@mzinga.io")

#For the DB
client = MongoClient(MONGODB_URI)
db = client["mzinga"]

#For processing the body message
def serialize_slate(nodes):
    if not nodes:
        return ""
    html = ""
    for node in nodes:
        if "children" in node:
            node_type = node.get("type", "")
            children_html = serialize_children(node.get("children", []))
            if node_type == "h1":
                html += f"<h1>{children_html}</h1>"
            elif node_type == "h2":
                html += f"<h2>{children_html}</h2>"
            elif node_type == "h3":
                html += f"<h3>{children_html}</h3>"
            elif node_type == "ul":
                html += f"<ul>{children_html}</ul>"
            elif node_type == "ol":
                html += f"<ol>{children_html}</ol>"
            elif node_type == "li":
                html += f"<li>{children_html}</li>"
            elif node_type == "link":
                url = node.get("url", "")
                html += f'<a href="{url}">{children_html}</a>'
            else:
                html += f"<p>{children_html}</p>"
        else:
            html += serialize_leaf(node)
    return html

#For processing the body message
def serialize_children(children):
    html = ""
    for child in children:
        if "children" in child:
            html += serialize_slate([child])
        else:
            html += serialize_leaf(child)
    return html

#For processing the body message
def serialize_leaf(leaf):
    text = leaf.get("text", "")
    if leaf.get("bold"):
        text = f"<strong>{text}</strong>"
    if leaf.get("italic"):
        text = f"<em>{text}</em>"
    if leaf.get("underline"):
        text = f"<u>{text}</u>"
    return text

#Return the users involved in refs
def resolve_emails(refs):
    if not refs:
        return []
    ids = []
    for ref in refs:
        val = ref.get("value")
        if isinstance(val, dict):
            val = val.get("id") or val.get("_id")
        if isinstance(val, str):
            try:
                val = ObjectId(val)
            except Exception:
                pass
        if val:
            ids.append(val)
    users = db["users"].find({"_id": {"$in": ids}})
    return [u["email"] for u in users if u.get("email")]


def process_document(doc):
    doc_id = doc["_id"]
    db["communications"].update_one(
        {"_id": doc_id}, {"$set": {"status": "processing"}}
    )
    log.info(f"[worker] Processing {doc_id}")

    try:
        #Query the DB for obtaining the data
        to_emails = resolve_emails(doc.get("tos", []))
        if not to_emails:
            raise Exception("No valid recipient email addresses found")
        cc_emails = resolve_emails(doc.get("ccs", []))
        bcc_emails = resolve_emails(doc.get("bccs", []))
        subject = doc.get("subject", "(no subject)")
        body_html = serialize_slate(doc.get("body", []))

        #Send the email
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            for to in to_emails:
                msg = MIMEMultipart("alternative")
                msg["From"] = EMAIL_FROM
                msg["To"] = to
                msg["Subject"] = subject
                if cc_emails:
                    msg["Cc"] = ", ".join(cc_emails)
                if bcc_emails:
                    msg["Bcc"] = ", ".join(bcc_emails)
                msg.attach(MIMEText(body_html, "html"))

                recipients = [to] + cc_emails + bcc_emails
                server.sendmail(EMAIL_FROM, recipients, msg.as_string())
                log.info(f"[worker] Sent to {to}")

        #Update the state
        db["communications"].update_one(
            {"_id": doc_id}, {"$set": {"status": "sent"}}
        )
        log.info(f"[worker] Marked {doc_id} as sent")

    except Exception as e:
        log.error(f"[worker] Failed {doc_id}: {e}")
        db["communications"].update_one(
            {"_id": doc_id}, {"$set": {"status": "failed"}}
        )


def main():
    log.info(f"[worker] Starting — polling every {POLL_INTERVAL}s")
    while True:
        doc = db["communications"].find_one({"status": "pending"})
        if doc:
            process_document(doc)
        else:
            time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()

import os
import time
import smtplib
import logging
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import requests
from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

MZINGA_URL = os.getenv('MZINGA_URL', 'http://localhost:3000')
MZINGA_EMAIL = os.getenv('MZINGA_EMAIL')
MZINGA_PASSWORD = os.getenv('MZINGA_PASSWORD')
POLL_INTERVAL = int(os.getenv('POLL_INTERVAL_SECONDS', 5))
SMTP_HOST = os.getenv('SMTP_HOST', 'localhost')
SMTP_PORT = int(os.getenv('SMTP_PORT', 1025))
EMAIL_FROM = os.getenv('EMAIL_FROM', 'worker@mzinga.io')

token = None

#Login request for obtaining the bearer JWT 
def authenticate():
    global token
    logging.info('Authenticating with MZinga...')
    response = requests.post(
        f'{MZINGA_URL}/api/users/login',
        json={'email': MZINGA_EMAIL, 'password': MZINGA_PASSWORD}
    )
    response.raise_for_status()
    token = response.json()['token']
    logging.info('Authentication successful')

def get_headers():
    return {'Authorization': f'Bearer {token}'}

#Request the pending communications using the API
def get_pending_documents():
    response = requests.get(
        f'{MZINGA_URL}/api/communications',
        params={'where[status][equals]': 'pending', 'depth': 1},
        headers=get_headers()
    )
    if response.status_code == 401:
        authenticate()
        return get_pending_documents()
    response.raise_for_status()
    return response.json().get('docs', [])

#Update the status of the email
def update_status(doc_id, status):
    response = requests.patch(
        f'{MZINGA_URL}/api/communications/{doc_id}',
        json={'status': status},
        headers=get_headers()
    )
    if response.status_code == 401:
        authenticate()
        update_status(doc_id, status)
        return
    response.raise_for_status()

#Create the email body starting from the JSON format
def serialize_slate(nodes):
    if not nodes:
        return ''
    html = ''
    for node in nodes:
        if 'text' in node:
            text = node['text']
            if node.get('bold'):
                text = f'<strong>{text}</strong>'
            if node.get('italic'):
                text = f'<em>{text}</em>'
            html += text
        else:
            children = serialize_slate(node.get('children', []))
            t = node.get('type', 'paragraph')
            if t == 'paragraph':
                html += f'<p>{children}</p>'
            elif t == 'h1':
                html += f'<h1>{children}</h1>'
            elif t == 'h2':
                html += f'<h2>{children}</h2>'
            elif t == 'ul':
                html += f'<ul>{children}</ul>'
            elif t == 'li':
                html += f'<li>{children}</li>'
            elif t == 'link':
                url = node.get('url', '#')
                html += f'<a href="{url}">{children}</a>'
            else:
                html += f'<p>{children}</p>'
    return html

#Extract the emails from the JSON object
def extract_emails(relationships):
    if not relationships:
        return []
    emails = []
    for rel in relationships:
        value = rel.get('value')
        if isinstance(value, dict):
            email = value.get('email')
            if email:
                emails.append(email)
    return emails

def send_email(to_list, cc_list, bcc_list, subject, html_body):
    msg = MIMEMultipart('alternative')
    msg['From'] = EMAIL_FROM
    msg['To'] = ', '.join(to_list)
    msg['Subject'] = subject
    if cc_list:
        msg['Cc'] = ', '.join(cc_list)
    if bcc_list:
        msg['Bcc'] = ', '.join(bcc_list)
    msg.attach(MIMEText(html_body, 'html'))
    all_recipients = to_list + cc_list + bcc_list
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.sendmail(EMAIL_FROM, all_recipients, msg.as_string())

#Create the email starting from the JSON format
def process_document(doc):
    doc_id = doc['id']
    logging.info(f'Processing document {doc_id}')
    update_status(doc_id, 'processing')
    try:
        to_emails = extract_emails(doc.get('tos', []))
        cc_emails = extract_emails(doc.get('ccs') or [])
        bcc_emails = extract_emails(doc.get('bccs') or [])
        html_body = serialize_slate(doc.get('body', []))
        subject = doc.get('subject', '(no subject)')
        send_email(to_emails, cc_emails, bcc_emails, subject, html_body)
        update_status(doc_id, 'sent')
        logging.info(f'Document {doc_id} sent successfully')
    except Exception as e:
        update_status(doc_id, 'failed')
        logging.error(f'Document {doc_id} failed: {e}')

def main():
    authenticate()
    logging.info('Worker started, polling for pending documents...')
    while True:
        docs = get_pending_documents()
        if docs:
            for doc in docs:
                process_document(doc)
        else:
            time.sleep(POLL_INTERVAL)

if __name__ == '__main__':
    main()
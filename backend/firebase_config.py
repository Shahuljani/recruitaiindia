import firebase_admin
from firebase_admin import credentials, firestore, auth
import os
import json

firebase_key = json.loads(os.environ["FIREBASE_KEY"])

cred = credentials.Certificate(firebase_key)
firebase_admin.initialize_app(cred)

db = firestore.client()
firebase_auth = auth

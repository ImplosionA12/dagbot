"""Fixed configuration values used across every document the bot generates.

Edit `.env` (or set env vars) to change.
"""
import os
from dotenv import load_dotenv

load_dotenv()

CLUB_NAME = os.getenv('CLUB_NAME', 'Drushya Animation and Gaming Club')

# Author stamped into the metadata of every generated document (the templates
# otherwise carry over their original creator's name).
DOC_AUTHOR = os.getenv('DOC_AUTHOR', CLUB_NAME)
COORDINATOR = os.getenv('COORDINATOR', 'Dr. Shaik Kareemulla')
COORDINATOR_PHONE = os.getenv('COORDINATOR_PHONE', '')
EMP_ID = os.getenv('EMP_ID', '70474')

ALLOWED_USER_ID = int(os.getenv('ALLOWED_USER_ID', '5422521708'))


def fixed_profile() -> dict:
    """Returns the fixed identity used in every flow."""
    return {
        'club': CLUB_NAME,
        'coordinator': COORDINATOR,
        'coordinator_phone': COORDINATOR_PHONE,
        'emp_id': EMP_ID,
    }

from flask_login import LoginManager
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_wtf.csrf import CSRFProtect

from models import db

login_manager = LoginManager()
limiter = Limiter(key_func=get_remote_address, default_limits=[])
csrf = CSRFProtect()

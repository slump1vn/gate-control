import os
from pathlib import Path
from decouple import config

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = config('SECRET_KEY', default='django-insecure-change-me-in-production')

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = config('DEBUG', default=True, cast=bool)

ALLOWED_HOSTS = config('ALLOWED_HOSTS', default='localhost,127.0.0.1', cast=lambda v: [s.strip() for s in v.split(',')])

# CSRF Trusted Origins for cross-origin requests

# Application definition
INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django_apscheduler',
    'corsheaders',
    'lpr_app',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    # Serves STATIC_ROOT (Django admin CSS/JS) in production, where Django itself does not
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'corsheaders.middleware.CorsMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'lpr_project.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'lpr_project.wsgi.application'

# Database
# Use environment variable for database location, fallback to project root
DATABASE_PATH = config('DATABASE_PATH', default=str(BASE_DIR / 'db.sqlite3'), cast=str)
DATABASE_DIR = Path(DATABASE_PATH).parent
LOG_DIR = DATABASE_DIR

# Ensure directories exist
if not DATABASE_DIR.exists():
    os.makedirs(DATABASE_DIR)

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': DATABASE_PATH,
    }
}

# Password validation
AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]

# Internationalization
LANGUAGE_CODE = 'en-us'
TIME_ZONE = config('TIME_ZONE', default='Asia/Ho_Chi_Minh')
USE_I18N = True
USE_TZ = True

# Static files (CSS, JavaScript, Images)
STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'

# WhiteNoise compresses collected files and serves them with far-future caching.
# Not the manifest variant: a missing reference in a third-party file would 500.
STORAGES = {
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    'staticfiles': {'BACKEND': 'whitenoise.storage.CompressedStaticFilesStorage'},
}

# Ensure static directory exists for development
if not os.path.exists('/app'):
    # Development environment - ensure lpr_app/static directory exists
    static_app_dir = BASE_DIR / 'lpr_app' / 'static'
    os.makedirs(static_app_dir, exist_ok=True)

STATICFILES_DIRS = [
    BASE_DIR / 'lpr_app' / 'static',
]

# Media files (Uploaded images)
MEDIA_URL = '/media/'
# Use environment variable for media location, fallback to project root
MEDIA_ROOT = str(config('MEDIA_PATH', default=str(BASE_DIR / 'media'), cast=str))

# Ensure media directory exists
MEDIA_DIR = Path(MEDIA_ROOT)
if not MEDIA_DIR.exists():
    os.makedirs(MEDIA_DIR)

# File upload settings
UPLOAD_FILE_MAX_SIZE = config('UPLOAD_FILE_MAX_SIZE', default=2097152, cast=int)  # 2MB
# Keep accepted uploads in memory instead of spooling them to temp files
FILE_UPLOAD_MAX_MEMORY_SIZE = UPLOAD_FILE_MAX_SIZE
# File parts are excluded from this check, so it does not limit image size
DATA_UPLOAD_MAX_MEMORY_SIZE = 250 * 1024  # 250KB

# Allowed file types for upload
ALLOWED_IMAGE_TYPES = ['jpeg', 'jpg', 'png', 'webp']

# Detection pipeline settings
MIN_PLATE_HEIGHT = config('MIN_PLATE_HEIGHT', default=30, cast=int)
PLATE_HEIGHT_FRACTION = config('PLATE_HEIGHT_FRACTION', default=0.05, cast=float)

CORS_ALLOWED_ORIGINS = config('CORS_ALLOWED_ORIGINS', default='http://localhost:3000', cast=lambda v: [s.strip() for s in v.split(',') if s.strip()])
CORS_ALLOW_PRIVATE_NETWORK = config('CORS_ALLOW_PRIVATE_NETWORK', default=False, cast=bool)
# The SPA uses session cookies for admin login and may be served from another
# origin of the same site (e.g. a different port), so CORS carries credentials
# for the explicitly allowed origins, and those origins are CSRF-trusted.
CORS_ALLOW_CREDENTIALS = True
CSRF_TRUSTED_ORIGINS = config('CSRF_TRUSTED_ORIGINS', default='', cast=lambda v: [s.strip() for s in v.split(',') if s.strip()]) or CORS_ALLOWED_ORIGINS
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = 'Lax'
CSRF_COOKIE_SAMESITE = 'Lax'
SESSION_COOKIE_SECURE = config('SESSION_COOKIE_SECURE', default=False, cast=bool)
CSRF_COOKIE_SECURE = SESSION_COOKIE_SECURE

# Behind a proxy that terminates TLS, Django sees plain HTTP. Without this it
# builds http:// URLs for an https:// site, and treats a secure request as
# insecure. Only turn it on when a proxy really sets X-Forwarded-Proto, since
# a client could otherwise claim to be on HTTPS.
if config('USE_X_FORWARDED_PROTO', default=False, cast=bool):
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
USE_X_FORWARDED_HOST = config('USE_X_FORWARDED_HOST', default=False, cast=bool)

RATE_LIMIT_ENABLE = config('RATE_LIMIT_ENABLE', default=True, cast=bool)
RATE_LIMIT_RATE = config('RATE_LIMIT_RATE', default='2/min', cast=str)
RATE_LIMIT_EXCLUDE_PATHS = config('RATE_LIMIT_EXCLUDE_PATHS', default='/health/,/api/v1/health-light/', cast=lambda v: [s.strip() for s in v.split(',') if s.strip()])
RATE_LIMIT_INCLUDE_PATHS = config('RATE_LIMIT_INCLUDE_PATHS', default='/api/v1/ocr/', cast=lambda v: [s.strip() for s in v.split(',') if s.strip()])

import sys
if 'test' in sys.argv:
    RATE_LIMIT_ENABLE = False

MIDDLEWARE.append('lpr_app.middleware.rate_limit.RateLimitMiddleware')

# The upload-and-recognise tool on the home page. Off, it needs a login: an
# internet-facing gate deployment should not hand its model to strangers.
# The canary service posts to /api/v1/ocr/ without a login, so it needs this on.
PUBLIC_UPLOAD_ENABLED = config('PUBLIC_UPLOAD_ENABLED', default=False, cast=bool)

OCR_CROP_PADDING_PX = config('OCR_CROP_PADDING_PX', default=25, cast=int)

# Plate crops narrower than this are enlarged before OCR (0 disables)
OCR_CROP_MIN_WIDTH = config('OCR_CROP_MIN_WIDTH', default=480, cast=int)

PROCESSING_TIMEOUT_MINUTES = config('PROCESSING_TIMEOUT_MINUTES', default=5, cast=int)
MAX_RETRIES = config('MAX_RETRIES', default=2, cast=int)
RETRY_BATCH_SIZE = config('RETRY_BATCH_SIZE', default=5, cast=int)
RETRY_INTERVAL_MINUTES = config('RETRY_INTERVAL_MINUTES', default=5, cast=int)
RETRY_SCHEDULER_ENABLED = config('RETRY_SCHEDULER_ENABLED', default=True, cast=bool)

# Qwen3-VL API Configuration
QWEN_API_KEY = config('QWEN_API_KEY', default='')
QWEN_BASE_URL = config('QWEN_BASE_URL', default='https://ollama.computedsynergy.com/v1')
QWEN_MODEL = config('QWEN_MODEL', default='qwen3-vl-4b-instruct')

# Gate automation (barrier control from plate recognition)
GATE_MODE = config('GATE_MODE', default='shadow')  # shadow | live
GATE_BURST_FRAMES = config('GATE_BURST_FRAMES', default=3, cast=int)
GATE_CONSENSUS_MIN = config('GATE_CONSENSUS_MIN', default=2, cast=int)
GATE_MIN_CONFIDENCE = config('GATE_MIN_CONFIDENCE', default=0.80, cast=float)
GATE_DECIDE_TIMEOUT = config('GATE_DECIDE_TIMEOUT', default=8, cast=int)
GATE_WORKER_THREADS = config('GATE_WORKER_THREADS', default=3, cast=int)
GATE_COMMAND_TTL_SECONDS = config('GATE_COMMAND_TTL_SECONDS', default=15, cast=int)
# Which frames of a burst survive the decision. 'evidence' keeps the one the
# plate was read from and deletes the rest on the spot; 'denied' also keeps the
# whole burst when the barrier stayed shut, which is what you want to look at
# when a plate was misread; 'all' keeps every frame of every decision.
GATE_KEEP_FRAMES = config('GATE_KEEP_FRAMES', default='evidence', cast=str)

GATE_EVENT_RETENTION_DAYS = config('GATE_EVENT_RETENTION_DAYS', default=90, cast=int)
GATE_AUTO_CLOSE = config('GATE_AUTO_CLOSE', default='controller')  # controller | software
GATE_AUTO_CLOSE_SECONDS = config('GATE_AUTO_CLOSE_SECONDS', default=10, cast=int)
GATE_HEARTBEAT_TIMEOUT_SECONDS = config('GATE_HEARTBEAT_TIMEOUT_SECONDS', default=30, cast=int)
GATE_AGENT_TOKEN = config('GATE_AGENT_TOKEN', default='')
GATE_CONFIG_ENCRYPTION_KEY = config('GATE_CONFIG_ENCRYPTION_KEY', default='')
# The live view never hits the camera more often than this, however fast viewers
# refresh and however many of them there are. Cameras also serve the gate agent,
# and some answer HTTP 500 when snapshots are requested too quickly.
GATE_SNAPSHOT_CACHE_SECONDS = config('GATE_SNAPSHOT_CACHE_SECONDS', default=1.0, cast=float)

GATE_CAMERA_ALLOWED_CIDRS = config(
    'GATE_CAMERA_ALLOWED_CIDRS',
    default='10.0.0.0/8,172.16.0.0/12,192.168.0.0/16',
    cast=lambda v: [s.strip() for s in v.split(',') if s.strip()],
)

# Default primary key field type
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# Security settings
SECURE_BROWSER_XSS_FILTER = True
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = 'DENY'

# Email settings (for error notifications, optional)
EMAIL_BACKEND = 'django.core.mail.backends.console.EmailBackend'

# Logging configuration

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'verbose': {
            'format': '{levelname} {asctime} {module} {process:d} {thread:d} {message}',
            'style': '{',
        },
    },
    'handlers': {
        'file': {
            'level': 'INFO',
            'class': 'logging.FileHandler',
            'filename': LOG_DIR / 'django.log',
            'formatter': 'verbose',
        },
        'console': {
            'level': 'INFO',
            'class': 'logging.StreamHandler',
            'formatter': 'verbose',
        },
    },
    'root': {
        'handlers': ['console', 'file'],
        'level': 'INFO',
    },
    'loggers': {
        'django': {
            'handlers': ['console', 'file'],
            'level': 'INFO',
            'propagate': False,
        },
        'lpr_app': {
            'handlers': ['console', 'file'],
            'level': 'DEBUG',
            'propagate': False,
        },
    },
}
# Canary Configuration
CANARY_ENABLED = config('CANARY_ENABLED', default='true', cast=bool)
CANARY_HEADER_NAME = config('CANARY_HEADER_NAME', default='X-Canary-Request')
CANARY_HEADER_VALUE = config('CANARY_HEADER_VALUE', default='random-string-not-known-outside')
CANARY_INTERVAL = config('CANARY_INTERVAL', default='900', cast=int)

PROMETHEUS_URL = config('PROMETHEUS_URL', default='http://prometheus:9090')

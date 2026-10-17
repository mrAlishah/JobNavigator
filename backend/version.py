"""The released version (major.minor), shown in Settings and on /health.

Its own module, free of imports, so Dockerfile.backend can stamp it into the
image at build time (/opt/jobnavigator/image-version) without loading the app;
image_check.py compares that stamp with this value at startup.
"""
APP_VERSION = "3.0"

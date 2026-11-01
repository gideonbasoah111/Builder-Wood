"""
Cloudinary configuration.

Reads credentials from builder_wood.app.config.settings (which pulls them
from environment variables / .env). Called once at import time from main.py.

Set these on Render (Environment tab) or in local .env:
    CLOUDINARY_CLOUD_NAME
    CLOUDINARY_API_KEY
    CLOUDINARY_API_SECRET

Find values: Cloudinary Console -> Dashboard (cloud name)
             Cloudinary Console -> Settings -> API Keys (key + secret)
"""
import cloudinary
import cloudinary.uploader
import cloudinary.api

from builder_wood.app.config import settings


cloudinary.config(
    cloud_name=settings.CLOUDINARY_CLOUD_NAME,
    api_key=settings.CLOUDINARY_API_KEY,
    api_secret=settings.CLOUDINARY_API_SECRET,
    secure=True,
    # If your Cloudinary account enforces SHA-256, uncomment:
    # signature_algorithm="sha256",
)
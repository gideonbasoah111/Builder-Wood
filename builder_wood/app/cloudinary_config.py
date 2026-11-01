"""
Cloudinary configuration.

The Python SDK automatically reads the CLOUDINARY_URL environment variable
when cloudinary.config() is called. Just importing this module ensures the
SDK is configured before any upload happens.

Set CLOUDINARY_URL in Render's environment variables:
    cloudinary://API_KEY:API_SECRET@CLOUD_NAME

To find it: Cloudinary Console -> Settings -> API Keys -> "Cloudinary URL"
"""
import cloudinary
import cloudinary.uploader
import cloudinary.api

# Ensure the SDK uses HTTPS URLs (secure_url) for all uploads.
# CLOUDINARY_URL env var is read automatically by cloudinary.config().
cloudinary.config(secure=True)
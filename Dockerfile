# Litmus Digital Signage - Docker Container
FROM python:3.11-slim

# Install system dependencies
RUN apt-get update && apt-get install -y \
    # LibreOffice for PPTX to PDF conversion
    libreoffice \
    # ImageMagick for PDF to PNG conversion
    imagemagick \
    # VLC for video playback (optional for HDMI mode)
    vlc \
    # Additional dependencies
    fonts-liberation \
    fonts-dejavu \
    curl \
    # Cleanup
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Configure ImageMagick to allow PDF processing (if file exists)
RUN if [ -f /etc/ImageMagick-6/policy.xml ]; then \
    sed -i 's/<policy domain="coder" rights="none" pattern="PDF" \/>/<policy domain="coder" rights="read|write" pattern="PDF" \/>/g' /etc/ImageMagick-6/policy.xml; \
    fi

# Set working directory
WORKDIR /app

# Copy requirements and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY dashboard.py .
COPY web_player.py .
COPY templates/ ./templates/

# Create necessary directories with proper permissions
RUN mkdir -p /root/signage/content/videos \
    /root/signage/content/presentations \
    /root/signage/cache/slides \
    /root/signage/logs \
    /root/signage/logo \
    /root/signage/commands \
    /root/signage/uploads_tmp \
    && chmod -R 755 /root/signage

# Expose ports
# 5000 - Dashboard (main control panel)
# 8080 - Web Player (for network TVs)
EXPOSE 5000 8080

# Health check for dashboard
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:5000').read()" || exit 1

# Environment variables (can be overridden)
ENV FLASK_ENV=production \
    PYTHONUNBUFFERED=1

# Default command: start dashboard
CMD ["python", "dashboard.py"]

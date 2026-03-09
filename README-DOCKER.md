# Litmus Digital Signage - Docker Deployment

## Quick Start

### 1. Build and Run with Docker Compose (Recommended)

```bash
# Build and start all services
docker-compose up -d

# View logs
docker-compose logs -f

# Stop all services
docker-compose down
```

### 2. Access the Application

- **Dashboard (Admin)**: http://localhost:5000
  - Username: `admin`
  - Password: `signage`
  
- **Web Player (TVs)**: http://localhost:8080
  - Point your network TVs/browsers to this URL

## Docker Commands

### Build Only
```bash
docker-compose build
```

### Start Services
```bash
# Start all services
docker-compose up -d

# Start specific service
docker-compose up -d dashboard
docker-compose up -d web-player
```

### Stop Services
```bash
# Stop all services
docker-compose down

# Stop and remove volumes (WARNING: deletes all content!)
docker-compose down -v
```

### View Logs
```bash
# All services
docker-compose logs -f

# Specific service
docker-compose logs -f dashboard
docker-compose logs -f web-player
```

### Restart Services
```bash
# Restart all
docker-compose restart

# Restart specific service
docker-compose restart dashboard
```

### Execute Commands in Container
```bash
# Open shell in dashboard container
docker-compose exec dashboard bash

# Check Python version
docker-compose exec dashboard python --version

# Run a Python command
docker-compose exec dashboard python -c "import flask; print(flask.__version__)"
```

## Advanced Configuration

### Environment Variables

Create a `.env` file in the same directory:

```env
# Flask Configuration
FLASK_ENV=production
FLASK_DEBUG=0

# Ports (if you need to change defaults)
DASHBOARD_PORT=5000
WEB_PLAYER_PORT=8080
```

Then reference in docker-compose.yml:
```yaml
ports:
  - "${DASHBOARD_PORT}:5000"
```

### Custom Logo

1. Create a `logo` folder in the project root
2. Add your `logo.png` file (recommended size: 500x500px)
3. The logo will be automatically mounted to the container

### Persistent Data

Data is stored in Docker volumes:
- `signage-content` - Uploaded videos/presentations
- `signage-cache` - Converted presentation slides
- `signage-logs` - Application logs
- `signage-data` - Configuration and playlists
### Playlists Store (New)

- Canonical store: `~/signage/playlists.json` inside the container.
- Legacy single playlist file `~/signage/playlist.json` is still supported. On first run, if `playlists.json` does not exist and `playlist.json` is a list, the app will create `playlists.json` with a single playlist named `default` and set it active. The legacy file is not deleted.
- Web Player:
  - `GET /api/playlists` returns playlist metadata and active id.
  - `GET /api/playlist?playlist_id=<optional>` returns the requested playlist; when omitted, returns the active playlist (legacy behavior).
- Dashboard provides CRUD endpoints under `/api/playlists/*` and UI controls for creating, selecting active, renaming, duplicating, deleting, and reordering.

To backup:
```bash
docker run --rm -v signage-content:/data -v $(pwd):/backup ubuntu tar czf /backup/signage-backup.tar.gz /data
```

To restore:
```bash
docker run --rm -v signage-content:/data -v $(pwd):/backup ubuntu tar xzf /backup/signage-backup.tar.gz -C /
```

## Production Deployment

### With Nginx Reverse Proxy

```nginx
# /etc/nginx/sites-available/signage
server {
    listen 80;
    server_name signage.example.com;

    location / {
        proxy_pass http://localhost:5000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        
        # Increase timeouts for large file uploads
        proxy_read_timeout 300;
        proxy_connect_timeout 300;
        proxy_send_timeout 300;
        
        # WebSocket support (if needed)
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_header_upgrade;
        proxy_set_header Connection "upgrade";
    }

    client_max_body_size 2G;
}

server {
    listen 80;
    server_name player.example.com;

    location / {
        proxy_pass http://localhost:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

### With SSL (Let's Encrypt)

```bash
# Install certbot
sudo apt-get install certbot python3-certbot-nginx

# Get SSL certificate
sudo certbot --nginx -d signage.example.com -d player.example.com
```

### Using Gunicorn (Production WSGI Server)

Modify `Dockerfile` CMD:
```dockerfile
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "4", "--timeout", "300", "dashboard:app"]
```

## Troubleshooting

### Port Already in Use
```bash
# Find process using port 5000
sudo lsof -i :5000

# Kill the process
sudo kill -9 <PID>
```

### Permission Issues
```bash
# Fix volume permissions
docker-compose exec dashboard chown -R root:root /root/signage
docker-compose exec dashboard chmod -R 755 /root/signage
```

### Container Won't Start
```bash
# Check container logs
docker-compose logs dashboard

# Remove and rebuild
docker-compose down
docker-compose build --no-cache
docker-compose up -d
```

### ImageMagick PDF Policy Error
Already configured in Dockerfile, but if issues persist:
```bash
docker-compose exec dashboard bash
sed -i 's/rights="none" pattern="PDF"/rights="read|write" pattern="PDF"/g' /etc/ImageMagick-6/policy.xml
```

## System Requirements

- **CPU**: 2+ cores recommended
- **RAM**: 2GB minimum, 4GB recommended
- **Disk**: 20GB+ for content storage
- **Network**: 100Mbps+ for smooth video streaming

## Monitoring

### Check Container Stats
```bash
docker stats
```

### Health Check Status
```bash
docker-compose ps
```

### Container Resource Usage
```bash
docker-compose top
```

## Updating

```bash
# Pull latest code
git pull

# Rebuild containers
docker-compose build --no-cache

# Restart with new image
docker-compose up -d
```

## Security Recommendations

1. **Change Default Password**: Access dashboard and change admin password
2. **Use HTTPS**: Deploy with SSL certificate in production
3. **Firewall**: Restrict access to ports 5000 and 8080
4. **Network**: Use private network for TVs
5. **Backups**: Regular backups of Docker volumes

## Support

For issues, check:
- Container logs: `docker-compose logs`
- Application logs: Inside `signage-logs` volume
- System stats: Dashboard → Stats button

---

**🚀 Your Digital Signage System is Ready!**

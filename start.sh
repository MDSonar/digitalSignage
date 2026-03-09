#!/bin/bash
# Litmus Digital Signage - Quick Start Script

echo "🚀 Litmus Digital Signage - Docker Setup"
echo "========================================"
echo ""

# Check if Docker is installed
if ! command -v docker &> /dev/null; then
    echo "❌ Docker is not installed. Please install Docker first:"
    echo "   https://docs.docker.com/get-docker/"
    exit 1
fi

# Check if Docker Compose is installed
if ! command -v docker-compose &> /dev/null; then
    echo "❌ Docker Compose is not installed. Please install Docker Compose first:"
    echo "   https://docs.docker.com/compose/install/"
    exit 1
fi

echo "✅ Docker and Docker Compose are installed"
echo ""

# Create .env file if it doesn't exist
if [ ! -f .env ]; then
    echo "📝 Creating .env file from template..."
    cp .env.example .env
    echo "✅ Created .env file (please review and customize)"
    echo ""
fi

# Create logo directory if it doesn't exist
if [ ! -d logo ]; then
    echo "📁 Creating logo directory..."
    mkdir -p logo
    echo "   Add your logo.png file to the 'logo' folder"
    echo ""
fi

# Ask user what to do
echo "What would you like to do?"
echo "1) Build and start (production)"
echo "2) Build and start (development with hot-reload)"
echo "3) Stop services"
echo "4) View logs"
echo "5) Clean up (remove containers and volumes)"
echo ""
read -p "Enter choice [1-5]: " choice

case $choice in
    1)
        echo ""
        echo "🔨 Building and starting services (production)..."
        docker-compose up -d --build
        echo ""
        echo "✅ Services started!"
        echo "   Dashboard: http://localhost:5000 (admin/signage)"
        echo "   Web Player: http://localhost:8080"
        echo ""
        echo "📊 To view logs: docker-compose logs -f"
        ;;
    2)
        echo ""
        echo "🔨 Building and starting services (development)..."
        docker-compose -f docker-compose.yml -f docker-compose.dev.yml up -d --build
        echo ""
        echo "✅ Services started in development mode!"
        echo "   Dashboard: http://localhost:5000 (admin/signage)"
        echo "   Web Player: http://localhost:8080"
        echo "   Code changes will auto-reload"
        echo ""
        echo "📊 To view logs: docker-compose logs -f"
        ;;
    3)
        echo ""
        echo "⏹️  Stopping services..."
        docker-compose down
        echo "✅ Services stopped"
        ;;
    4)
        echo ""
        echo "📊 Viewing logs (Ctrl+C to exit)..."
        docker-compose logs -f
        ;;
    5)
        echo ""
        read -p "⚠️  This will delete all containers and volumes. Continue? [y/N]: " confirm
        if [ "$confirm" = "y" ] || [ "$confirm" = "Y" ]; then
            echo "🗑️  Cleaning up..."
            docker-compose down -v
            docker system prune -f
            echo "✅ Cleanup complete"
        else
            echo "❌ Cleanup cancelled"
        fi
        ;;
    *)
        echo "❌ Invalid choice"
        exit 1
        ;;
esac

echo ""
echo "========================================"
echo "📚 For more commands, see README-DOCKER.md"

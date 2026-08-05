.PHONY: help install run dev docker-up docker-down clean

help:
	@echo "OmniBridge Makefile"
	@echo ""
	@echo "Usage:"
	@echo "  make install      Install dependencies"
	@echo "  make dev          Run the server in development mode (reload on changes)"
	@echo "  make run          Run the server in production mode"
	@echo "  make docker-up    Start the application using Docker Compose"
	@echo "  make docker-down  Stop the Docker Compose containers"
	@echo "  make clean        Remove Python cache and data files"

install:
	pip install -r requirements.txt

dev:
	uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload

run:
	uvicorn app.main:app --host 0.0.0.0 --port 8000

docker-up:
	docker-compose up -d --build

docker-down:
	docker-compose down

clean:
	find . -type d -name "__pycache__" -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete
	rm -rf .pytest_cache

# Minimal image with Python 3.12
FROM python:3.12-slim

# Create a non-root user with UID 1000 (Hugging Face Requirement)
RUN useradd -m -u 1000 user

# tgcrypto ko build karne ke liye gcc aur python3-dev install karein
RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc python3-dev && \
    rm -rf /var/lib/apt/lists/*

# Switch to the new user
USER user
ENV PATH="/home/user/.local/bin:$PATH"

# Ensure logs are not buffered
ENV PYTHONUNBUFFERED=1

# Default port
ENV PORT=7860

# Set Working Directory
WORKDIR /home/user/app

# Copy requirements and install them securely
COPY --chown=user requirements.txt requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy all project files and give ownership to the user
COPY --chown=user . /home/user/app

# Expose default port
EXPOSE 7860

# Start the bot
CMD ["python", "bot.py"]

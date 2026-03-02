FROM evmbench/base:latest

# Install Pi agent and httpx for the worker
RUN python3 -m pip install --no-cache-dir httpx loguru uvloop
RUN npm install -g @mariozechner/pi-coding-agent

# Copy worker code
COPY docker/worker/init.py /tmp/init.py
COPY worker_runner/ /opt/evmbench/worker_runner/
RUN chmod +x /opt/evmbench/worker_runner/*.sh

CMD ["python3", "/tmp/init.py"]

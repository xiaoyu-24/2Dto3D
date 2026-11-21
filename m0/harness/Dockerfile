FROM python:3.11-slim-bookworm@sha256:e8c69d86735f6df3efa06b324694fe51f0422785f83a3bd463052636c2421944
WORKDIR /opt/probe
COPY requirements.lock .
RUN pip install --disable-pip-version-check --no-cache-dir --only-binary=:all: --require-hashes -r requirements.lock && pip check
COPY profile.patch.yml probe.py ./
COPY skills/ ./skills/
ENV HOME=/tmp PYTHONDONTWRITEBYTECODE=1
USER 65534:65534
CMD ["timeout", "90", "python", "probe.py"]

# grace/switchboard — docker CLI + compose plugin + python3, nothing else.
FROM docker:cli
RUN apk add --no-cache python3 py3-pip && pip install --no-cache-dir --break-system-packages segno   # QR for TOTP enrolment
COPY switchboard.py /app/switchboard.py
ENV PYTHONUNBUFFERED=1
ENTRYPOINT ["python3", "/app/switchboard.py"]

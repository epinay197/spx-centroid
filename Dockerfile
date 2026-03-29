FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV SERVER_MODE=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY spx_centroid.py .
COPY spx_centroid.html .

EXPOSE 8765

CMD ["python", "spx_centroid.py"]

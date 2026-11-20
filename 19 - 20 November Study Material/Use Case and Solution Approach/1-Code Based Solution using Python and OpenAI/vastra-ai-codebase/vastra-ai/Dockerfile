FROM python:3.12-slim
WORKDIR /srv
RUN useradd -m vastra
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
USER vastra
ENV VASTRA_ENV=prod
EXPOSE 8000
HEALTHCHECK CMD python -c "import urllib.request;urllib.request.urlopen('http://localhost:8000/health')"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]

FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    UV_NO_VENV=1 \
    MAKTAB=1


WORKDIR /app

# Install uv
RUN pip install -i https://mirror-pypi.runflare.com/simple --upgrade pip

# Copy dependency files
COPY requirements.txt .

# Install dependencies
RUN pip install -i https://mirror-pypi.runflare.com/simple -r requirements.txt

# Copy project
COPY . .

RUN python manage.py collectstatic --no-input

CMD [ "python", "manage.py", "runserver", "0.0.0.0:8000"]

EXPOSE 8000

FROM python:3.10-slim-buster

WORKDIR /app

COPY requirements.txt requirements.txt
RUN pip3 install -r requirements.txt

COPY . .

EXPOSE 80

CMD [ "python3", "-m", "uvicorn", "api:app", "--host", "0.0.0.0", "--port", "80"]
FROM python:3.10

WORKDIR /home/user/app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py coze_client.py parser.py store.py app.py ./
COPY static/ ./static/

EXPOSE 7860

ENTRYPOINT ["python", "-u", "app.py"]

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from datetime import datetime

app = FastAPI()

app.state.available_power = 0


@app.get("/")
async def root():
    return HTMLResponse(
        content=''.join(
            map(str, open('template.html').readlines())
        ),
        status_code=200
    )


@app.get("/get_power")
async def get_power():
    return {
        'success': True,
        'time': datetime.now(),
        'power': app.state.available_power
    }


@app.post("/set_power/{power}")
async def set_power(power: int):
    app.state.available_power = power

    return {
        'success': True
    }

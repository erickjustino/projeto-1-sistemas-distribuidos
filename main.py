import time
import uuid
import heapq
import random
import requests
import asyncio
import os
import socket
from typing import Dict, Any, Optional, List, Set
from pydantic import BaseModel
from fastapi import FastAPI, BackgroundTasks
from contextlib import asynccontextmanager
from threading import Lock

# ==========================================
# 1. CONFIGURAÇÕES GERAIS
# ==========================================
NUM_PROCESSES = 3 
SERVICE_NAME = "process-svc" 
HOSTNAME = socket.gethostname() # Ex: process-0

# Extrai o Rank (ID numérico) do hostname. Ex: process-2 -> 2
try:
    MY_RANK = int(HOSTNAME.split("-")[-1])
except:
    MY_RANK = 0 # Fallback

# Verifica se o modo de teste (atraso) está ativo
TEST_MODE_ACTIVE = os.environ.get("DELAY_ACK", "false").lower() == "true"
IS_SLOW_NODE = TEST_MODE_ACTIVE and ("process-2" in HOSTNAME)

# Relógio Lógico Global (Lamport)
LOGICAL_CLOCK = 0
STATE_LOCK = Lock()

# Variável para saber quem é o Líder Atual
CURRENT_LEADER = None

def update_clock(received_time: int = 0):
    global LOGICAL_CLOCK
    LOGICAL_CLOCK = max(LOGICAL_CLOCK, received_time) + 1
    return LOGICAL_CLOCK

class Message(BaseModel):
    sender_id: str
    logical_clock: int
    req_id: Optional[str] = None      
    payload: Optional[Dict] = None    
    req_type: Optional[str] = None    

@asynccontextmanager
async def lifespan(app: FastAPI):
    print(f"🚀 [{HOSTNAME}]  Iniciado.")
    yield

app = FastAPI(lifespan=lifespan)

# Função auxiliar de envio
async def send_http(url: str, payload: dict, timeout=5):
    try:
        await asyncio.to_thread(requests.post, url, json=payload, timeout=timeout)
        return True
    except Exception as e:
        return False

async def broadcast(endpoint: str, msg: Message, exclude_self: bool = False):
    payload = msg.model_dump()
    tasks = []
    for i in range(NUM_PROCESSES):
        target_host = f"process-{i}.{SERVICE_NAME}"
        if exclude_self and target_host == f"{HOSTNAME}.{SERVICE_NAME}":
            continue
        url = f"http://{target_host}:80{endpoint}"
        tasks.append(send_http(url, payload))
    await asyncio.gather(*tasks)

# ==========================================
# 2. LÓGICA DO MULTICAST (Ordenação Total)
# ==========================================
MCAST_QUEUE: List[tuple] = []   
MCAST_ACKS: Dict[str, Set] = {} 
MCAST_PROCESSED: Set = set()    

async def check_mcast_queue():
    if not MCAST_QUEUE: return
    head = MCAST_QUEUE[0]
    head_clock, head_sender, head_req_id, head_payload = head
    
    if head_req_id in MCAST_PROCESSED:
        heapq.heappop(MCAST_QUEUE)
        return

    acks = MCAST_ACKS.get(head_req_id, set())
    if len(acks) >= NUM_PROCESSES:
        with STATE_LOCK:
            heapq.heappop(MCAST_QUEUE)
            MCAST_PROCESSED.add(head_req_id)
        # ADICIONADO [{HOSTNAME}] AQUI
        print(f"✅ [{HOSTNAME}] [MULTICAST] Entregue: {head_payload.get('msg')} (C={head_clock})", flush=True)
        await check_mcast_queue()

@app.post("/mcast/start")
async def start_multicast(payload: Dict[str, Any]):
    req_id = str(uuid.uuid4())
    with STATE_LOCK:
        clock = update_clock()
        msg = Message(sender_id=HOSTNAME, logical_clock=clock, req_id=req_id, payload=payload)
    # ADICIONADO [{HOSTNAME}] AQUI
    print(f"📢 [{HOSTNAME}] [MULTICAST] Envio: {payload['msg']}", flush=True)
    await broadcast("/mcast/req", msg, exclude_self=False)
    return {"id": req_id}

@app.post("/mcast/req")
async def receive_mcast_req(msg: Message):
    with STATE_LOCK:
        update_clock(msg.logical_clock)
        item = (msg.logical_clock, msg.sender_id, msg.req_id, msg.payload)
        if item not in MCAST_QUEUE:
            heapq.heappush(MCAST_QUEUE, item)
            # ADICIONADO [{HOSTNAME}] AQUI
            print(f"📥 [{HOSTNAME}] [MULTICAST] Fila: {msg.payload.get('msg')} (C={msg.logical_clock})", flush=True)
        reply_clock = LOGICAL_CLOCK 
        reply_msg = Message(sender_id=HOSTNAME, logical_clock=reply_clock, req_id=msg.req_id)

    if IS_SLOW_NODE:
        print(f"zzz [{HOSTNAME}] [MULTICAST] Atrasando ACK por 15s...", flush=True)
        await asyncio.sleep(15)

    await broadcast("/mcast/ack", reply_msg, exclude_self=False)
    return {"status": "ok"}

@app.post("/mcast/ack")
async def receive_mcast_ack(msg: Message):
    with STATE_LOCK:
        update_clock(msg.logical_clock)
        if msg.req_id not in MCAST_ACKS: MCAST_ACKS[msg.req_id] = set()
        MCAST_ACKS[msg.req_id].add(msg.sender_id)
    await check_mcast_queue()
    return {"status": "ack_received"}

# ==========================================
# 3. LÓGICA DO MUTEX (Ricart-Agrawala)
# ==========================================
RELEASED, WANTED, HELD = "RELEASED", "WANTED", "HELD"
MUTEX_STATE = RELEASED
MUTEX_REQ_CLOCK = 0
MUTEX_DEFERRED: List[str] = []
MUTEX_ACKS_COUNT = 0
MUTEX_EVENT = asyncio.Event()

@app.post("/mutex/acquire")
async def acquire_mutex(bg_tasks: BackgroundTasks):
    global MUTEX_STATE, MUTEX_REQ_CLOCK, MUTEX_ACKS_COUNT
    with STATE_LOCK:
        if MUTEX_STATE != RELEASED: return {"error": "Ocupado"}
        MUTEX_STATE = WANTED
        MUTEX_REQ_CLOCK = update_clock()
        MUTEX_ACKS_COUNT = 0
        MUTEX_EVENT.clear()
        msg = Message(sender_id=HOSTNAME, logical_clock=MUTEX_REQ_CLOCK, req_type="REQUEST")
        print(f"✋ [{HOSTNAME}] [MUTEX] Solicitando SC (Clock: {MUTEX_REQ_CLOCK})", flush=True)

    await broadcast("/mutex/req", msg, exclude_self=True)
    await MUTEX_EVENT.wait()
    
    with STATE_LOCK:
        MUTEX_STATE = HELD
        print(f"🔐 [{HOSTNAME}] [MUTEX] ENTREI NA SC! (15s)", flush=True)
    await asyncio.sleep(15)
    
    print(f"👋 [{HOSTNAME}] [MUTEX] Saindo da SC...", flush=True)
    with STATE_LOCK:
        MUTEX_STATE = RELEASED
        deferred_copy = list(MUTEX_DEFERRED)
        MUTEX_DEFERRED.clear()
        
    clock = update_clock()
    for target in deferred_copy:
        reply = Message(sender_id=HOSTNAME, logical_clock=clock, req_type="REPLY")
        target_url = f"http://{target}.{SERVICE_NAME}:80/mutex/ack"
        bg_tasks.add_task(send_http, target_url, reply.model_dump())
    return {"status": "SC Finalizada"}

@app.post("/mutex/req")
async def receive_mutex_req(msg: Message):
    global MUTEX_STATE
    should_defer = False
    with STATE_LOCK:
        update_clock(msg.logical_clock)
        if MUTEX_STATE == HELD: should_defer = True
        elif MUTEX_STATE == WANTED:
            my_priority = (MUTEX_REQ_CLOCK < msg.logical_clock) or \
                          ((MUTEX_REQ_CLOCK == msg.logical_clock) and (HOSTNAME < msg.sender_id))
            if my_priority: should_defer = True
        
        if should_defer:
            print(f"⏳ [{HOSTNAME}] [MUTEX] Adiando {msg.sender_id}", flush=True)
            MUTEX_DEFERRED.append(msg.sender_id)
        else:
            print(f"👍 [{HOSTNAME}] [MUTEX] Permitindo {msg.sender_id}", flush=True)

    if not should_defer:
        reply = Message(sender_id=HOSTNAME, logical_clock=LOGICAL_CLOCK, req_type="REPLY")
        target_url = f"http://{msg.sender_id}.{SERVICE_NAME}:80/mutex/ack"
        asyncio.create_task(send_http(target_url, reply.model_dump()))
    return {"status": "ok"}

@app.post("/mutex/ack")
async def receive_mutex_ack(msg: Message):
    global MUTEX_ACKS_COUNT
    with STATE_LOCK:
        update_clock(msg.logical_clock)
        MUTEX_ACKS_COUNT += 1
        if MUTEX_ACKS_COUNT >= NUM_PROCESSES - 1: MUTEX_EVENT.set()
    return {"status": "ack"}


# ==========================================
# 4. LÓGICA DE ELEIÇÃO (BULLY ALGORITHM)
# ==========================================

@app.get("/bully/leader")
async def get_leader():
    return {"current_leader": CURRENT_LEADER, "my_rank": MY_RANK}

@app.post("/bully/start")
async def start_election_manual(bg_tasks: BackgroundTasks):
    """Endpoint manual para forçar uma eleição (ex: quando percebe que líder caiu)"""
    print(f"🗳️ [{HOSTNAME}] [BULLY] Iniciei eleição manual!", flush=True)
    bg_tasks.add_task(run_election)
    return {"status": "Eleição Iniciada"}

@app.post("/bully/election")
async def receive_election_msg(msg: Message, bg_tasks: BackgroundTasks):
    """Recebi mensagem de ELEIÇÃO de alguém"""
    sender_rank = int(msg.sender_id.split("-")[-1])
    
    print(f"📩 [{HOSTNAME}] [BULLY] Recebi ELEIÇÃO de {msg.sender_id} (Rank {sender_rank})", flush=True)
    
    # Se meu rank for maior, eu respondo OK e tomo a frente da eleição
    if MY_RANK > sender_rank:
        # Inicia minha própria eleição em background para 'calar' o menor
        bg_tasks.add_task(run_election)
        return {"status": "ok"} # Respondo OK (Eu sou maior, deixa comigo)
    else:
        # Se eu sou menor (teoricamente impossível receber Election de maior no Bully clássico, mas...)
        return {"status": "ok"}

@app.post("/bully/coordinator")
async def receive_coordinator(msg: Message):
    """Recebi mensagem do NOVO LÍDER"""
    global CURRENT_LEADER
    CURRENT_LEADER = msg.sender_id
    print(f"👑 [{HOSTNAME}] [BULLY] Novo Líder Aclamado: {CURRENT_LEADER}", flush=True)
    return {"status": "ack"}

async def run_election():
    """Lógica do Valentão: Envia Election para todos com ID maior"""
    global CURRENT_LEADER
    
    higher_nodes = []
    # Descobre quem é maior que eu
    for i in range(MY_RANK + 1, NUM_PROCESSES):
        higher_nodes.append(f"process-{i}")
    
    if not higher_nodes:
        # Se não tem ninguém maior, EU SOU O LÍDER!
        print(f"😎 [{HOSTNAME}] [BULLY] Sou o maior (Rank {MY_RANK})! Declarando vitória...", flush=True)
        await announce_victory()
        return

    print(f"⚡ [{HOSTNAME}] [BULLY] Enviando desafio para maiores: {higher_nodes}", flush=True)
    
    payload = Message(sender_id=HOSTNAME, logical_clock=update_clock()).model_dump()
    any_higher_alive = False
    
    for node in higher_nodes:
        url = f"http://{node}.{SERVICE_NAME}:80/bully/election"
        # Timeout curto (1s) pq eleição tem que ser ágil
        success = await send_http(url, payload, timeout=1.0)
        if success:
            any_higher_alive = True
            print(f"🏳️ [{HOSTNAME}] [BULLY] {node} respondeu. Desisto e aguardo.", flush=True)
            return

    # Se ninguém maior respondeu (timeout ou erro), EU ganhei
    if not any_higher_alive:
        print(f"💀 [{HOSTNAME}] [BULLY] Ninguém maior respondeu. Assumindo liderança!", flush=True)
        await announce_victory()

async def announce_victory():
    global CURRENT_LEADER
    CURRENT_LEADER = HOSTNAME
    msg = Message(sender_id=HOSTNAME, logical_clock=update_clock())
    # Manda para TODOS (menores e maiores, broadcast total)
    print(f"📣 [{HOSTNAME}] [BULLY] Enviando COORDINATOR para todos!", flush=True)
    await broadcast("/bully/coordinator", msg, exclude_self=False)
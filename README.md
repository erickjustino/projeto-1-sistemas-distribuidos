# 🌐 Sistemas Distribuídos: Multicast, Mutex & Eleição em Kubernetes

Este projeto implementa três algoritmos fundamentais de Sistemas Distribuídos, orquestrados em um cluster **Kubernetes** (Minikube). O objetivo é demonstrar coordenação, consistência e tolerância a falhas em um ambiente distribuído containerizado. O projeto foi desenvolvido no Google Cloud Shell.

---

## 🚀 Funcionalidades Implementadas

O sistema consiste em 3 nós (`process-0`, `process-1`, `process-2`) que se comunicam via HTTP REST, implementando:

### 1. Multicast com Ordenação Total 📨
* **Algoritmo:** Relógios Lógicos de Lamport + Fila de Prioridade.
* **Objetivo:** Garantir que todos os nós processem as mensagens exatamente na mesma ordem, independente de latência na rede.
* **Feature:** Simulação de atraso (Delay) configurável para provar a ordenação.

### 2. Exclusão Mútua Distribuída (Mutex) 🔒
* **Algoritmo:** Ricart-Agrawala.
* **Objetivo:** Garantir que apenas um processo acesse a Seção Crítica (Recurso Compartilhado) por vez.
* **Lógica:** Baseado em permissões explícitas e timestamp (quem pediu primeiro ganha).

### 3. Eleição de Líder 👑
* **Algoritmo:** O Valentão (Bully Algorithm).
* **Objetivo:** Eleger um coordenador para o cluster de forma dinâmica.
* **Tolerância a Falhas:** Se o líder "morre" (Pod deletado), os nós remanescentes detectam e elegem um novo líder automaticamente.

---

## 🛠️ Tecnologias Utilizadas

* **Linguagem:** Python 3.10
* **Framework:** FastAPI (Async)
* **Orquestração:** Kubernetes (StatefulSet + Headless Service)
* **Infraestrutura Local:** Minikube (Multi-node profile)
* **Containerização:** Docker

---

## 📂 Estrutura do Projeto

``` text
├── Dockerfile           # Definição da imagem Docker (Python + Deps)
├── k8s.yaml             # Manifesto Kubernetes (StatefulSet e Service)
├── main.py              # Código fonte unificado (Servidor e Lógica dos Algoritmos)
└── requirements.txt     # Dependências (FastAPI, Requests, Uvicorn)
```

## ⚡ Como Executar

### 1. Pré-requisitos
* Bash (Linux) - WSL funciona normalmente
* Docker
* Minikube
* Kubectl

### 2. Iniciar e Deploy
Baixe o projeto e execute os passos abaixo sequencialmente no seu terminal:

```bash
# 1. Start do minikube com perfil multinode (2 nós, 4GB RAM, 2 CPUs)
minikube start --nodes 2 --memory 4g --cpus 2 -p multinode-cluster
eval $(minikube docker-env -p multinode-cluster)

# 2. Build da imagem
docker build -t process:version-final .

# 3. Carregar imagem para o cluster específico
minikube image load process:version-final -p multinode-cluster

# 4. Subir os Pods
kubectl apply -f k8s.yaml

# 5. Verificar se os pods subiram
kubectl get pods
# Status esperado: Running (3/3)
```

## 🧪 Roteiro de Testes

Para visualizar os logs de cada processo, abra uma aba de terminal para cada um:
```bash
kubectl logs -f process-0
kubectl logs -f process-1
kubectl logs -f process-2
```

1. Multicast (Ordenação Total)
Verifica se mensagens chegam na mesma ordem para todos.

OBS: Se DELAY_ACK: "true" (no k8s.yaml), haverá um atraso antes da entrega final, provando que o sistema aguarda o nó lento.

```bash
kubectl exec -it process-0 -- curl -X POST http://localhost/mcast/start \
-H "Content-Type: application/json" -d '{"msg": "Teste Multicast"}'
```

2. Exclusão Mútua (Ricart-Agrawala)
Simula dois processos tentando acessar um recurso crítico ao mesmo tempo.

```bash
kubectl exec process-0 -- curl -X POST http://localhost/mutex/acquire -d '{}' & \
kubectl exec process-1 -- curl -X POST http://localhost/mutex/acquire -d '{}'
```
Resultado Esperado: Um entra (🔐 ENTREI), processa e sai (👋 Saindo). Só então o segundo entra. Nunca os dois ao mesmo tempo.


3. Eleição de Líder (Valentão/Bully)
O nó com maior ID (Rank) deve ser o líder.

A. Eleição Normal:
```bash
kubectl exec -it process-0 -- curl -X POST http://localhost/bully/start -d '{}'
```
Resultado: process-2 (maior ID) vence.


B. Falha do Líder:
Derrubar o líder atual
```bash
kubectl delete pod process-2
```
Forçar nova eleição (Rapidamente, antes dele voltar)
```bash
kubectl exec -it process-0 -- curl -X POST http://localhost/bully/start -d '{}'
```
Resultado: process-1 assume a liderança na ausência do 2.

C. Recuperação: Aguarde o process-2 voltar ao status Running (Self-healing do Kubernetes) e inicie a eleição novamente.
```bash
kubectl exec -it process-0 -- curl -X POST http://localhost/bully/start -d '{}'
```
Resultado: process-2 retoma a liderança.

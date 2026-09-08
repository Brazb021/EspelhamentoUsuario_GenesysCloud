import os
import sys
import time
import pandas as pd
import requests
from dotenv import load_dotenv

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

load_dotenv()

CLIENT_ID = os.getenv("GENESYS_CLIENT_ID")
CLIENT_SECRET = os.getenv("GENESYS_CLIENT_SECRET")
REGION = os.getenv("GENESYS_REGION", "sae1")
BASE_USER_EMAIL = os.getenv("GENESYS_BASE_USER_EMAIL", "")

CSV_USERS = "usuarios.csv"
CSV_PHONES = "telefones.csv"
DELAY_BETWEEN_REQUESTS = 0.6
MAX_RETRIES = 3
REQUIRED_COLUMNS = {"nome", "email", "senha"}
REQUIRED_PHONE_COLUMNS = {"name", "base", "site", "email"}
STATION_LOOKUP_ATTEMPTS = 5
STATION_LOOKUP_WAIT = 2

# Domínio base por região. Adicione outras conforme necessário.
REGION_HOSTS = {
    "sae1": "sae1.pure.cloud",          # São Paulo
    "us_east_1": "mypurecloud.com",
    "us_west_2": "usw2.pure.cloud",
    "eu_west_1": "mypurecloud.ie",
    "eu_west_2": "euw2.pure.cloud",
    "ap_southeast_2": "mypurecloud.com.au",
    "ca_central_1": "cac1.pure.cloud",
}


def get_access_token():
    domain = REGION_HOSTS.get(REGION)
    if not domain:
        print(f"❌ Região '{REGION}' não reconhecida. Configure GENESYS_REGION no .env.")
        return None, None

    token_url = f"https://login.{domain}/oauth/token"
    api_base = f"https://api.{domain}"

    payload = {
        "grant_type": "client_credentials",
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
    }
    headers = {"Content-Type": "application/x-www-form-urlencoded"}

    response = requests.post(token_url, data=payload, headers=headers)
    if response.status_code == 200:
        return response.json()["access_token"], api_base
    else:
        print(f"❌ Erro ao obter token: {response.status_code} - {response.text}")
        return None, None


def request_with_retry(method, url, **kwargs):
    response = None
    for attempt in range(MAX_RETRIES):
        response = requests.request(method, url, **kwargs)
        if response.status_code == 429:
            wait = int(response.headers.get("Retry-After", 5))
            print(f"   ⏳ Rate limit atingido. Aguardando {wait}s... (tentativa {attempt + 1}/{MAX_RETRIES})")
            time.sleep(wait)
            continue
        return response
    return response


def search_user_by_email(token, api_base, email):
    url = f"{api_base}/api/v2/users/search"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    body = {"query": [{"fields": ["email"], "value": email, "type": "EXACT"}]}

    response = request_with_retry("POST", url, json=body, headers=headers)
    if response.status_code == 200:
        results = response.json().get("results", [])
        return results[0] if results else None

    print(f"⚠️  Erro ao buscar usuário base '{email}': {response.status_code} - {response.text}")
    return None


def get_home_division_id(token, api_base):
    url = f"{api_base}/api/v2/authorization/divisions/home"
    headers = {"Authorization": f"Bearer {token}"}

    response = request_with_retry("GET", url, headers=headers)
    if response.status_code != 200:
        print(f"⚠️  Erro ao obter divisão home: {response.status_code} - {response.text}")
        return None

    return response.json().get("id")


def get_role_assignments(token, api_base, user_id, home_division_id):
    url = f"{api_base}/api/v2/authorization/subjects/{user_id}"
    headers = {"Authorization": f"Bearer {token}"}

    response = request_with_retry("GET", url, headers=headers)
    if response.status_code == 404:
        return []
    if response.status_code != 200:
        print(f"⚠️  Erro ao obter roles do usuário base: {response.status_code} - {response.text}")
        return []

    grants = response.json().get("grants", [])
    assignments = []
    for grant in grants:
        role = grant.get("role") or {}
        division = grant.get("division") or {}
        role_id = role.get("id")
        if not role_id:
            continue
        # divisionId é obrigatório na API; roles globais (sem divisão) usam a divisão home
        assignments.append({"roleId": role_id, "divisionId": division.get("id") or home_division_id})
    return assignments


def get_user_details(token, api_base, user_id):
    """Retorna (acdAutoAnswer, divisionId) do usuário base numa única chamada."""
    url = f"{api_base}/api/v2/users/{user_id}"
    headers = {"Authorization": f"Bearer {token}"}

    response = request_with_retry("GET", url, headers=headers)
    if response.status_code != 200:
        print(f"⚠️  Erro ao obter dados do usuário base: {response.status_code} - {response.text}")
        return False, None

    data = response.json()
    acd_auto_answer = bool(data.get("acdAutoAnswer", False))
    division_id = (data.get("division") or {}).get("id")
    return acd_auto_answer, division_id


def apply_acd_auto_answer(token, api_base, user_id, enabled):
    url = f"{api_base}/api/v2/users/bulk"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    body = [{"id": user_id, "acdAutoAnswer": enabled}]

    response = request_with_retry("PATCH", url, json=body, headers=headers)
    if response.status_code in (200, 202, 204):
        return True

    print(f"   ❌ Erro ao aplicar ACD Auto Answer no usuário {user_id}: {response.status_code} - {response.text}")
    return False


def get_user_queue_ids(token, api_base, user_id):
    headers = {"Authorization": f"Bearer {token}"}
    ids = []
    page = 1

    while True:
        url = f"{api_base}/api/v2/users/{user_id}/queues?pageSize=100&pageNumber={page}"
        response = request_with_retry("GET", url, headers=headers)

        if response.status_code == 404:
            return []
        if response.status_code != 200:
            print(f"⚠️  Erro ao obter filas do usuário base: {response.status_code} - {response.text}")
            return ids

        data = response.json()
        ids.extend(e["id"] for e in data.get("entities", []))

        if page >= data.get("pageCount", 1):
            break
        page += 1

    return ids


def apply_queues(token, api_base, user_id, queue_ids):
    if not queue_ids:
        return True

    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    all_ok = True

    for qid in queue_ids:
        # PATCH /users/{id}/queues só alterna "joined" para quem já é membro da fila;
        # para adicionar membro novo é preciso o endpoint da fila (queue-centric).
        url = f"{api_base}/api/v2/routing/queues/{qid}/members"
        response = request_with_retry("POST", url, json=[{"id": user_id}], headers=headers)
        if response.status_code not in (200, 202, 204):
            print(f"   ❌ Erro ao associar usuário à fila {qid}: {response.status_code} - {response.text}")
            all_ok = False

    return all_ok


def create_user(token, api_base, name, email, password, division_id=None):
    url = f"{api_base}/api/v2/users"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    body = {"name": name, "email": email, "password": password}
    if division_id:
        body["divisionId"] = division_id

    response = request_with_retry("POST", url, json=body, headers=headers)
    if response.status_code in (200, 201):
        return response.json()["id"]

    print(f"   ❌ Erro ao criar usuário '{email}': {response.status_code} - {response.text}")
    return None


def apply_roles(token, api_base, user_id, role_assignments):
    if not role_assignments:
        return True

    url = f"{api_base}/api/v2/authorization/subjects/{user_id}/bulkadd"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    response = request_with_retry("POST", url, json={"grants": role_assignments}, headers=headers)
    if response.status_code in (200, 204):
        return True

    print(f"   ❌ Erro ao aplicar roles no usuário {user_id}: {response.status_code} - {response.text}")
    return False


def get_phone_base_settings_id_by_name(token, api_base, name):
    url = f"{api_base}/api/v2/telephony/providers/edges/phonebasesettings"
    headers = {"Authorization": f"Bearer {token}"}

    response = request_with_retry("GET", url, params={"name": name, "pageSize": 25}, headers=headers)
    if response.status_code != 200:
        print(f"   ❌ Erro ao buscar Phone Base Settings '{name}': {response.status_code} - {response.text}")
        return None

    entities = response.json().get("entities", [])
    for entity in entities:
        if entity.get("name", "").strip().lower() == name.strip().lower():
            return entity["id"]

    return entities[0]["id"] if entities else None


def get_site_id_by_name(token, api_base, name):
    url = f"{api_base}/api/v2/telephony/providers/edges/sites"
    headers = {"Authorization": f"Bearer {token}"}

    response = request_with_retry("GET", url, params={"name": name, "pageSize": 25}, headers=headers)
    if response.status_code != 200:
        print(f"   ❌ Erro ao buscar Site '{name}': {response.status_code} - {response.text}")
        return None

    entities = response.json().get("entities", [])
    for entity in entities:
        if entity.get("name", "").strip().lower() == name.strip().lower():
            return entity["id"]

    return entities[0]["id"] if entities else None


def create_webrtc_phone(token, api_base, user_id, phone_name, phone_base_settings_id, site_id):
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    template_url = f"{api_base}/api/v2/telephony/providers/edges/phones/template"
    response = request_with_retry(
        "GET", template_url, params={"phoneBaseSettingsId": phone_base_settings_id}, headers=headers
    )
    if response.status_code != 200:
        print(f"   ❌ Erro ao obter template de telefone: {response.status_code} - {response.text}")
        return None

    phone_body = response.json()
    phone_body["name"] = phone_name
    phone_body["webRtcUser"] = {"id": user_id}
    phone_body["site"] = {"id": site_id}

    create_url = f"{api_base}/api/v2/telephony/providers/edges/phones"
    response = request_with_retry("POST", create_url, json=phone_body, headers=headers)
    if response.status_code not in (200, 201):
        print(f"   ❌ Erro ao criar telefone '{phone_name}': {response.status_code} - {response.text}")
        return None

    return response.json()["id"]


def find_station_by_webrtc_user(token, api_base, user_id):
    url = f"{api_base}/api/v2/stations"
    headers = {"Authorization": f"Bearer {token}"}

    for _ in range(STATION_LOOKUP_ATTEMPTS):
        response = request_with_retry("GET", url, params={"webRtcUserId": user_id}, headers=headers)
        if response.status_code == 200:
            entities = response.json().get("entities", [])
            if entities:
                return entities[0]["id"]
        time.sleep(STATION_LOOKUP_WAIT)

    return None


def assign_default_station(token, api_base, user_id, station_id):
    url = f"{api_base}/api/v2/users/{user_id}/station/defaultstation/{station_id}"
    headers = {"Authorization": f"Bearer {token}"}

    response = request_with_retry("PUT", url, headers=headers)
    if response.status_code in (200, 202, 204):
        return True

    print(f"   ❌ Erro ao atribuir estação padrão: {response.status_code} - {response.text}")
    return False


def read_phones_csv(csv_path):
    """Espera um CSV com cabeçalho: name,base,site,email."""
    df = pd.read_csv(csv_path, dtype=str).fillna("")
    df.columns = [c.strip().lower() for c in df.columns]

    missing = REQUIRED_PHONE_COLUMNS - set(df.columns)
    if missing:
        raise ValueError(f"CSV de telefones sem as colunas obrigatórias: {', '.join(sorted(missing))}")

    rows = []
    for _, row in df.iterrows():
        name = row["name"].strip()
        base = row["base"].strip()
        site = row["site"].strip()
        email = row["email"].strip()

        if not name or not base or not site or not email:
            print(f"   ⚠️  Linha ignorada (dados incompletos): {row.to_dict()}")
            continue

        rows.append({"name": name, "base": base, "site": site, "email": email})
    return rows


def criar_telefones(token, api_base):
    if not os.path.exists(CSV_PHONES):
        return

    try:
        phones = read_phones_csv(CSV_PHONES)
    except Exception as e:
        print(f"❌ Erro ao ler CSV de telefones: {e}")
        return

    if not phones:
        return

    print(f"\n=== Criando telefones WebRTC ({len(phones)}) ===\n")

    base_settings_cache = {}
    site_cache = {}

    for idx, phone in enumerate(phones, 1):
        name, base, site, email = phone["name"], phone["base"], phone["site"], phone["email"]
        print(f"[{idx}/{len(phones)}] Telefone '{name}' -> {email}")

        user = search_user_by_email(token, api_base, email)
        if not user:
            print(f"   ❌ Usuário '{email}' não encontrado. Pulando.")
            continue

        if base not in base_settings_cache:
            base_settings_cache[base] = get_phone_base_settings_id_by_name(token, api_base, base)
        phone_base_settings_id = base_settings_cache[base]
        if not phone_base_settings_id:
            print(f"   ❌ Phone Base Settings '{base}' não encontrado. Pulando.")
            continue

        if site not in site_cache:
            site_cache[site] = get_site_id_by_name(token, api_base, site)
        site_id = site_cache[site]
        if not site_id:
            print(f"   ❌ Site '{site}' não encontrado. Pulando.")
            continue

        phone_id = create_webrtc_phone(token, api_base, user["id"], name, phone_base_settings_id, site_id)
        if not phone_id:
            continue

        print(f"   ✅ Telefone '{name}' criado! ID: {phone_id}")

        station_id = find_station_by_webrtc_user(token, api_base, user["id"])
        if not station_id:
            print("   ⚠️  Telefone criado, mas a estação não apareceu a tempo (atribua manualmente depois)")
            time.sleep(DELAY_BETWEEN_REQUESTS)
            continue

        if assign_default_station(token, api_base, user["id"], station_id):
            print("   ✅ Telefone atribuído como estação padrão")
        else:
            print("   ⚠️  Falha ao atribuir como estação padrão")

        time.sleep(DELAY_BETWEEN_REQUESTS)


def read_users_csv(csv_path):
    """Espera um CSV com cabeçalho: nome,email,senha."""
    df = pd.read_csv(csv_path, dtype=str).fillna("")
    df.columns = [c.strip().lower() for c in df.columns]

    missing = REQUIRED_COLUMNS - set(df.columns)
    if missing:
        raise ValueError(f"CSV sem as colunas obrigatórias: {', '.join(sorted(missing))}")

    rows = []
    for _, row in df.iterrows():
        nome = row["nome"].strip()
        email = row["email"].strip()
        senha = row["senha"].strip()

        if not nome or not email or not senha:
            print(f"   ⚠️  Linha ignorada (dados incompletos): {row.to_dict()}")
            continue

        rows.append({"nome": nome, "email": email, "senha": senha})
    return rows


def main():
    print("=== Genesys Cloud - Criação de Usuários em Massa ===\n")

    if not CLIENT_ID or not CLIENT_SECRET:
        print("❌ GENESYS_CLIENT_ID / GENESYS_CLIENT_SECRET não configurados no .env")
        return

    token, api_base = get_access_token()
    if not token:
        return

    try:
        users = read_users_csv(CSV_USERS)
    except Exception as e:
        print(f"❌ Erro ao ler CSV de usuários: {e}")
        return

    print(f"Total de usuários a criar: {len(users)}\n")

    home_division_id = get_home_division_id(token, api_base)

    role_assignments = []
    queue_ids = []
    acd_auto_answer = False
    division_id = None

    if BASE_USER_EMAIL:
        base_user = search_user_by_email(token, api_base, BASE_USER_EMAIL)
        if base_user:
            role_assignments = get_role_assignments(token, api_base, base_user["id"], home_division_id)
            queue_ids = get_user_queue_ids(token, api_base, base_user["id"])
            acd_auto_answer, division_id = get_user_details(token, api_base, base_user["id"])
            print(
                f"Usuário base '{BASE_USER_EMAIL}': {len(role_assignments)} função(ões), "
                f"{len(queue_ids)} fila(s), ACD Auto Answer={acd_auto_answer}\n"
            )
        else:
            print(f"⚠️  Usuário base '{BASE_USER_EMAIL}' não encontrado. Nada será espelhado.\n")
    else:
        print("⚠️  GENESYS_BASE_USER_EMAIL não configurado no .env. Nada será espelhado.\n")

    success = 0
    failed = 0

    for idx, user in enumerate(users, 1):
        nome, email, senha = user["nome"], user["email"], user["senha"]
        print(f"[{idx}/{len(users)}] Criando usuário: {email}")

        new_user_id = create_user(token, api_base, nome, email, senha, division_id)
        if not new_user_id:
            failed += 1
            time.sleep(DELAY_BETWEEN_REQUESTS)
            continue

        print(f"   ✅ Usuário criado! ID: {new_user_id}")

        if role_assignments:
            if apply_roles(token, api_base, new_user_id, role_assignments):
                print(f"   ✅ {len(role_assignments)} função(ões) espelhada(s)")
            else:
                print("   ⚠️  Falha ao aplicar as funções")

        if queue_ids:
            if apply_queues(token, api_base, new_user_id, queue_ids):
                print(f"   ✅ {len(queue_ids)} fila(s) espelhada(s)")
            else:
                print("   ⚠️  Falha ao associar as filas")

        if acd_auto_answer:
            if apply_acd_auto_answer(token, api_base, new_user_id, True):
                print("   ✅ ACD Auto Answer ativado")
            else:
                print("   ⚠️  Falha ao ativar o ACD Auto Answer")

        success += 1
        time.sleep(DELAY_BETWEEN_REQUESTS)

    print("\n=== Processo Finalizado ===")
    print(f"✅ Usuários criados com sucesso: {success}")
    print(f"❌ Falhas: {failed}")

    criar_telefones(token, api_base)


if __name__ == "__main__":
    main()

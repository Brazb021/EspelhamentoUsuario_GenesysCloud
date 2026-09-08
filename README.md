# Espelhamento de Usuários - Genesys Cloud

Script em Python para automação e espelhamento de usuários no Genesys Cloud, garantindo a consistência de cadastros e dados.

## Pré-requisitos

- Python 3.8 ou superior
- Bibliotecas necessárias (instaladas via `pip`):
  - `requests` (ou a biblioteca oficial da Genesys Cloud)
  - `python-dotenv`

## Configuração

1. Clone o repositório:
   ```bash
   git clone [https://github.com/Brazb021/EspelhamentoUsuario_GenesysCloud.git](https://github.com/Brazb021/EspelhamentoUsuario_GenesysCloud.git)


Crie um arquivo .env na raiz do projeto com as suas credenciais da API da Genesys Cloud:

GENESYS_CLIENT_ID=seu_client_id_aqui
GENESYS_CLIENT_SECRET=seu_client_secret_aqui
GENESYS_REGION=sua_regiao_aqui


Execute o script principal diretamente pelo terminal:
python Espelhamento_user.py


Segurança
Os arquivos contendo dados sensíveis (.env, *.exe, telefones.csv, usuarios.csv) são ignorados automaticamente pelo controle de versão através do .gitignore.

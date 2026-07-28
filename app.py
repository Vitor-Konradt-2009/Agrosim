# app.py
import base64
import importlib.util
import io
import os
import shutil
import subprocess
import sys
import time
import uuid
from collections import OrderedDict

import matplotlib
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import numpy as np
import pandas as pd
import requests
from dotenv import load_dotenv
from flask import (
    Flask,
    flash,
    redirect,
    render_template_string,
    request,
    send_file,
    session,
    url_for,
)

# =========================
# Auto-instalação de dependências
# =========================
REQUIRED_PACKAGES = {
    "flask": "Flask>=3.0",
    "numpy": "numpy>=1.26",
    "pandas": "pandas>=2.2",
    "matplotlib": "matplotlib>=3.8",
    "openpyxl": "openpyxl>=3.1",
    "dotenv": "python-dotenv>=1.0",
    "requests": "requests>=2.31",
}


def _is_module_available(module_name):
    return importlib.util.find_spec(module_name) is not None


def _install_package(spec):
    subprocess.check_call([sys.executable, "-m", "pip", "install", spec])


def ensure_dependencies():
    missing_specs = []

    for module_name, package_spec in REQUIRED_PACKAGES.items():
        if not _is_module_available(module_name):
            missing_specs.append(package_spec)

    if missing_specs:
        print(f"[AGROSIM] Instalando dependências faltantes: {', '.join(missing_specs)}")
        for spec in missing_specs:
            _install_package(spec)
        print("[AGROSIM] Dependências instaladas com sucesso.")


ensure_dependencies()

matplotlib.use("Agg")

# =========================
# Inicialização de .env + logo
# =========================


def garantir_arquivo_env():
    if not os.path.exists(".env"):
        conteudo = (
            "GROQ_API_KEY=\n"
            "GROQ_MODEL=llama-3.1-8b-instant\n"
            "GROQ_VISION_MODEL=meta-llama/llama-4-scout-17b-16e-instruct\n"
            "GROQ_TIMEOUT=60\n"
            "AGROSIM_PASSWORD=2026\n"
            "AGROSIM_SECRET_KEY=troque-esta-chave-em-producao\n"
        )
        with open(".env", "w", encoding="utf-8") as f:
            f.write(conteudo)
        print("[AGROSIM] .env criado. Preencha GROQ_API_KEY para habilitar IA.")


def garantir_logo_padrao():
    """
    Garante que exista static/logo_agrosim.png.
    Se não existir, tenta copiar de nomes comuns na raiz.
    """
    os.makedirs("static", exist_ok=True)
    destino = os.path.join("static", "logo_agrosim.png")

    if os.path.exists(destino):
        return

    candidatos = [
        "Agrosim.png",
        "agrosim.png",
        "image_gen_output.png",
        "logo_agrosim.png",
    ]
    for nome in candidatos:
        if os.path.exists(nome):
            shutil.copyfile(nome, destino)
            print(f"[AGROSIM] Logo copiada de '{nome}' para 'static/logo_agrosim.png'")
            return

    print("[AGROSIM] Logo não encontrada na raiz. Coloque 'Agrosim.png' ao lado do app.py.")


garantir_arquivo_env()
garantir_logo_padrao()
load_dotenv(override=False)

# =========================
# Configurações
# =========================
CONFIG = {
    "n_simulacoes": 10_000,
    "dolar_volatilidade": 0.06,
    "frete_export_volatilidade": 0.20,
    "seed": 42,
    "tolerancia_empate_rs": 1e-6,
}

BUSHEL_TO_TON = 0.0272
SACA_TO_TON = 0.06

ACCESS_PASSWORD = os.getenv("AGROSIM_PASSWORD", "2026")

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.1-8b-instant").strip()
GROQ_VISION_MODEL = os.getenv(
    "GROQ_VISION_MODEL", "meta-llama/llama-4-scout-17b-16e-instruct"
).strip()
GROQ_TIMEOUT = int(os.getenv("GROQ_TIMEOUT", "60"))

MAX_LOGIN_TENTATIVAS = 5
BLOQUEIO_SEGUNDOS = 300

RESULTADOS_MAX = 40
RESULTADOS = OrderedDict()
TENTATIVAS_IP = {}

app = Flask(__name__)
app.secret_key = os.getenv("AGROSIM_SECRET_KEY", "troque-esta-chave-em-producao")

app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = False  # Em HTTPS, usar True.


# =========================
# Helpers
# =========================
def normalizar_float(valor, nome):
    try:
        return float(str(valor).replace(",", ".").strip())
    except Exception:
        raise ValueError(f"Campo '{nome}' inválido. Digite um número válido.") from None


def validar_entrada(valor, nome, minimo=0.0, inclusivo=False):
    if inclusivo:
        ok = valor >= minimo
        simbolo = ">="
    else:
        ok = valor > minimo
        simbolo = ">"

    if not ok:
        raise ValueError(f"'{nome}' deve ser {simbolo} {minimo}. Recebido: {valor}")

    return valor


def preparar_entradas(form):
    entradas = {
        "preco_cbot": normalizar_float(form.get("preco_cbot"), "Preço CBOT"),
        "premio_porto": normalizar_float(form.get("premio_porto"), "Prêmio de porto"),
        "dolar_medio": normalizar_float(form.get("dolar_medio"), "Dólar"),
        "frete_export_medio": normalizar_float(
            form.get("frete_export_medio"), "Frete exportação"
        ),
        "taxas_export_ton": normalizar_float(
            form.get("taxas_export_ton"), "Taxas exportação"
        ),
        "alqueires": normalizar_float(form.get("alqueires"), "Alqueires"),
        "sacas_por_alqueire": normalizar_float(
            form.get("sacas_por_alqueire"), "Sacas por alqueire"
        ),
        "insumos_total": normalizar_float(form.get("insumos_total"), "Insumos total"),
        "assistencia_total": normalizar_float(
            form.get("assistencia_total"), "Assistência técnica total"
        ),
        "preco_interno_saca": normalizar_float(
            form.get("preco_interno_saca"), "Preço interno por saca"
        ),
        "custos_interno_ton": normalizar_float(
            form.get("custos_interno_ton"), "Custos internos por ton"
        ),
    }

    validar_entrada(entradas["preco_cbot"], "Preço CBOT")
    validar_entrada(entradas["dolar_medio"], "Dólar")
    validar_entrada(entradas["frete_export_medio"], "Frete exportação")
    validar_entrada(entradas["taxas_export_ton"], "Taxas exportação", inclusivo=True)
    validar_entrada(entradas["alqueires"], "Alqueires")
    validar_entrada(entradas["sacas_por_alqueire"], "Sacas por alqueire")
    validar_entrada(entradas["insumos_total"], "Insumos total", inclusivo=True)
    validar_entrada(
        entradas["assistencia_total"], "Assistência técnica total", inclusivo=True
    )
    validar_entrada(entradas["preco_interno_saca"], "Preço interno por saca")
    validar_entrada(entradas["custos_interno_ton"], "Custos internos por ton", inclusivo=True)

    return entradas


def br_num(v, casas=2):
    return (
        f"{float(v):,.{casas}f}"
        .replace(",", "X")
        .replace(".", ",")
        .replace("X", ".")
    )


def br_money(v):
    return "R$ " + br_num(v, 2)


def get_client_ip():
    xff = request.headers.get("X-Forwarded-For", "")
    if xff:
        return xff.split(",")[0].strip()
    return request.remote_addr or "desconhecido"


def ip_bloqueado(ip):
    data = TENTATIVAS_IP.get(ip)
    if not data:
        return False, 0

    bloqueado_ate = data.get("bloqueado_ate", 0)
    agora = time.time()

    if agora < bloqueado_ate:
        return True, int(bloqueado_ate - agora)

    if data.get("tentativas", 0) >= MAX_LOGIN_TENTATIVAS and agora >= bloqueado_ate:
        TENTATIVAS_IP[ip] = {"tentativas": 0, "bloqueado_ate": 0}

    return False, 0


def registrar_tentativa_falha(ip):
    data = TENTATIVAS_IP.get(ip, {"tentativas": 0, "bloqueado_ate": 0})
    data["tentativas"] += 1

    if data["tentativas"] >= MAX_LOGIN_TENTATIVAS:
        data["bloqueado_ate"] = time.time() + BLOQUEIO_SEGUNDOS

    TENTATIVAS_IP[ip] = data


def resetar_tentativas(ip):
    TENTATIVAS_IP[ip] = {"tentativas": 0, "bloqueado_ate": 0}


def guardar_resultado(item):
    rid = str(uuid.uuid4())
    RESULTADOS[rid] = item
    RESULTADOS.move_to_end(rid)

    while len(RESULTADOS) > RESULTADOS_MAX:
        RESULTADOS.popitem(last=False)

    return rid


# =========================
# Núcleo de negócio
# =========================
def calcular_producao_total(alqueires, sacas_por_alqueire):
    sacas_totais = alqueires * sacas_por_alqueire
    toneladas_totais = sacas_totais * SACA_TO_TON
    return sacas_totais, toneladas_totais


def receita_export_ton(preco_cbot, premio, dolar):
    preco_total_usd_bushel = preco_cbot + premio
    return (preco_total_usd_bushel / BUSHEL_TO_TON) * dolar


def lucro_export_total(
    preco_cbot,
    premio,
    dolar,
    frete_export_ton,
    taxas_export_ton,
    toneladas_totais,
    custos_fixos_totais,
):
    receita_ton = receita_export_ton(preco_cbot, premio, dolar)
    margem_ton_antes_fixos = receita_ton - frete_export_ton - taxas_export_ton
    return margem_ton_antes_fixos * toneladas_totais - custos_fixos_totais


def lucro_interno_total(
    preco_interno_saca, custos_interno_ton, toneladas_totais, custos_fixos_totais
):
    preco_interno_ton = preco_interno_saca / SACA_TO_TON
    margem_ton_antes_fixos = preco_interno_ton - custos_interno_ton
    return float(margem_ton_antes_fixos * toneladas_totais - custos_fixos_totais)


def lognormal_params(media, desvio):
    sigma2 = np.log(1 + (desvio / media) ** 2)
    mu = np.log(media) - sigma2 / 2
    return mu, np.sqrt(sigma2)


def rodar_simulacao(entradas, config=CONFIG):
    rng = np.random.default_rng(config["seed"])
    n = config["n_simulacoes"]

    sacas_totais, toneladas_totais = calcular_producao_total(
        entradas["alqueires"], entradas["sacas_por_alqueire"]
    )
    custos_fixos_totais = entradas["insumos_total"] + entradas["assistencia_total"]

    dolar_std = entradas["dolar_medio"] * config["dolar_volatilidade"]
    frete_std = entradas["frete_export_medio"] * config["frete_export_volatilidade"]

    mu_d, sig_d = lognormal_params(entradas["dolar_medio"], dolar_std)
    mu_f, sig_f = lognormal_params(entradas["frete_export_medio"], frete_std)

    dolar_sim = rng.lognormal(mu_d, sig_d, n)
    frete_export_sim = rng.lognormal(mu_f, sig_f, n)

    lucro_export_sim = lucro_export_total(
        preco_cbot=entradas["preco_cbot"],
        premio=entradas["premio_porto"],
        dolar=dolar_sim,
        frete_export_ton=frete_export_sim,
        taxas_export_ton=entradas["taxas_export_ton"],
        toneladas_totais=toneladas_totais,
        custos_fixos_totais=custos_fixos_totais,
    )

    lucro_interno = lucro_interno_total(
        preco_interno_saca=entradas["preco_interno_saca"],
        custos_interno_ton=entradas["custos_interno_ton"],
        toneladas_totais=toneladas_totais,
        custos_fixos_totais=custos_fixos_totais,
    )

    df = pd.DataFrame(
        {
            "Dolar": dolar_sim,
            "Frete_Export": frete_export_sim,
            "Lucro_Export_Total": lucro_export_sim,
            "Lucro_Interno_Total": lucro_interno,
            "Diff_Export_Menos_Interno": lucro_export_sim - lucro_interno,
        }
    )

    contexto = {
        "sacas_totais": sacas_totais,
        "toneladas_totais": toneladas_totais,
        "custos_fixos_totais": custos_fixos_totais,
        "lucro_interno_total": lucro_interno,
        "n_simulacoes": n,
        "tolerancia_empate_rs": config["tolerancia_empate_rs"],
    }
    return df, contexto


def analisar_resultados(df, contexto):
    lucro_export = df["Lucro_Export_Total"]
    lucro_interno = contexto["lucro_interno_total"]
    diff = df["Diff_Export_Menos_Interno"]

    n = int(contexto["n_simulacoes"])
    tol = float(contexto["tolerancia_empate_rs"])

    prob_lucro_export = (lucro_export > 0).mean() * 100
    prob_lucro_interno = 100.0 if lucro_interno > 0 else 0.0

    mask_export_melhor = diff > tol
    mask_interno_melhor = diff < -tol
    mask_empate = ~(mask_export_melhor | mask_interno_melhor)

    cen_export_melhor = int(mask_export_melhor.sum())
    cen_interno_melhor = int(mask_interno_melhor.sum())
    cen_empate = int(mask_empate.sum())

    return {
        "sacas_totais": contexto["sacas_totais"],
        "toneladas_totais": contexto["toneladas_totais"],
        "custos_fixos_totais": contexto["custos_fixos_totais"],
        "n_simulacoes": n,
        "lucro_interno_total": lucro_interno,
        "lucro_export_medio": float(lucro_export.mean()),
        "lucro_export_p10": float(lucro_export.quantile(0.10)),
        "lucro_export_p90": float(lucro_export.quantile(0.90)),
        "lucro_export_std": float(lucro_export.std()),
        "diff_media": float(diff.mean()),
        "prob_lucro_export": float(prob_lucro_export),
        "prob_lucro_interno": float(prob_lucro_interno),
        "prob_export_melhor_que_interno": cen_export_melhor / n * 100,
        "prob_interno_melhor_que_export": cen_interno_melhor / n * 100,
        "prob_empate_tecnico": cen_empate / n * 100,
        "cenarios_export_melhor": cen_export_melhor,
        "cenarios_interno_melhor": cen_interno_melhor,
        "cenarios_empate_tecnico": cen_empate,
    }


def recomendacao(stats):
    p_exp = stats["prob_export_melhor_que_interno"]
    if p_exp >= 60:
        return "EXPORTAR (maior probabilidade de melhor resultado)."
    if p_exp >= 40:
        return "ZONA DE DECISÃO (equilíbrio entre risco e retorno)."
    return "MERCADO INTERNO tende a ser mais vantajoso."


# =========================
# IA (análise por imagem do gráfico)
# =========================
def gerar_explicacao_ia(grafico_b64):
    if not GROQ_API_KEY:
        return "Explicação automática indisponível: configure GROQ_API_KEY no .env."

    prompt_sistema = (
        "Você é um analista agrícola-financeiro especialista EXCLUSIVAMENTE em PRODUÇÃO DE SOJA. "
        "Contexto obrigatório: comparação econômica da soja entre exportação e mercado interno. "
        "NÃO mencione café, milho, trigo, algodão, pecuária ou outras culturas. "
        "Baseie-se principalmente na leitura VISUAL dos 4 gráficos da imagem. "
        "Não invente números exatos que não estejam legíveis. "
        "Se algum valor não puder ser lido, escreva: 'não legível no gráfico'. "
        "Responda em português do Brasil, linguagem simples e prática."
    )

    prompt_usuario = (
        "Analise a imagem com 4 gráficos do AGROSIM e explique:\n"
        "1) Resumo em 3 linhas\n"
        "2) O que cada gráfico (1 a 4) mostra visualmente\n"
        "3) Onde está o maior risco/variabilidade\n"
        "4) Interpretação prática para decisão na produção de soja\n"
        "5) Conclusão objetiva (somente soja)\n"
    )

    try:
        url = "https://api.groq.com/openai/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {GROQ_API_KEY}",
            "Content-Type": "application/json",
        }
        image_data_url = f"data:image/png;base64,{grafico_b64}"

        payload = {
            "model": GROQ_VISION_MODEL,
            "messages": [
                {"role": "system", "content": prompt_sistema},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt_usuario},
                        {"type": "image_url", "image_url": {"url": image_data_url}},
                    ],
                },
            ],
            "temperature": 0.2,
            "max_tokens": 900,
        }

        resp = requests.post(url, headers=headers, json=payload, timeout=GROQ_TIMEOUT)

        if resp.status_code in (401, 403):
            return "IA indisponível: chave Groq inválida ou sem permissão."
        if resp.status_code == 429:
            return "IA indisponível: limite da Groq atingido no plano gratuito. Tente mais tarde."
        if resp.status_code == 400:
            return (
                "Requisição inválida para análise de imagem. "
                "Verifique se GROQ_VISION_MODEL é um modelo com suporte a imagem na sua conta."
            )

        resp.raise_for_status()
        data = resp.json()

        texto = (
            data.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
        )

        if not texto:
            return "A IA não retornou texto nesta tentativa."

        texto_l = texto.lower()
        if "café" in texto_l or "cafe" in texto_l:
            texto += (
                "\n\n[Nota do sistema] Interprete esta análise exclusivamente no contexto "
                "de PRODUÇÃO DE SOJA."
            )

        return texto

    except requests.exceptions.Timeout:
        return "A geração da explicação demorou demais (timeout). Tente novamente."
    except Exception as e:
        return f"Não foi possível gerar a explicação por IA agora. Detalhe técnico: {e}"


# =========================
# Gráficos
# =========================
def gerar_graficos_base64(df, entradas, contexto):
    fig, axes = plt.subplots(2, 2, figsize=(16, 10), constrained_layout=True)
    fig.suptitle(
        "AGROSIM — Exportação vs Mercado Interno (SOJA)",
        fontsize=14,
        fontweight="bold",
    )

    cor_azul = "#1a73e8"
    cor_verde = "#188038"
    cor_vermelha = "#d93025"
    formatter_reais = mticker.FuncFormatter(lambda x, _: f"R$ {x:,.0f}")

    lucro_interno = contexto["lucro_interno_total"]
    toneladas_totais = contexto["toneladas_totais"]
    custos_fixos_totais = contexto["custos_fixos_totais"]

    # 1) Histograma do lucro da exportação
    ax1 = axes[0, 0]
    ax1.hist(
        df["Lucro_Export_Total"],
        bins=50,
        color=cor_azul,
        edgecolor="white",
        alpha=0.85,
    )
    ax1.axvline(
        0,
        color=cor_vermelha,
        linestyle="--",
        linewidth=1.4,
        label="Break-even (R$ 0)",
    )
    ax1.axvline(
        df["Lucro_Export_Total"].mean(),
        color="orange",
        linestyle="--",
        linewidth=1.4,
        label="Média export",
    )
    ax1.axvline(
        lucro_interno,
        color=cor_verde,
        linestyle="-.",
        linewidth=1.8,
        label="Lucro interno",
    )
    ax1.set_title("1) Distribuição do Lucro da Exportação")
    ax1.set_xlabel("Lucro total (R$)")
    ax1.set_ylabel("Frequência")
    ax1.xaxis.set_major_formatter(formatter_reais)
    ax1.tick_params(axis="x", rotation=20)
    ax1.legend(fontsize=8)

    # 2) Câmbio x lucro da exportação
    ax2 = axes[0, 1]
    sc = ax2.scatter(
        df["Dolar"],
        df["Lucro_Export_Total"],
        c=df["Diff_Export_Menos_Interno"],
        cmap="RdYlGn",
        alpha=0.35,
        s=9,
    )
    ax2.axhline(
        lucro_interno,
        color=cor_verde,
        linestyle="-.",
        linewidth=1.4,
        label="Lucro interno",
    )
    ax2.axhline(0, color=cor_vermelha, linestyle="--", linewidth=1.2)
    ax2.set_title("2) Câmbio vs Lucro da Exportação")
    ax2.set_xlabel("Dólar (R$/US$)")
    ax2.set_ylabel("Lucro total exportação (R$)")
    ax2.yaxis.set_major_formatter(formatter_reais)
    cbar = fig.colorbar(sc, ax=ax2, pad=0.02, fraction=0.05)
    cbar.set_label("Export - Interno (R$)")
    ax2.legend(fontsize=8)

    # 3 e 4) Curvas de vantagem
    dolar_min = entradas["dolar_medio"] * 0.75
    dolar_max = entradas["dolar_medio"] * 1.25
    dolar_range = np.linspace(dolar_min, dolar_max, 300)

    lucro_export_range = lucro_export_total(
        preco_cbot=entradas["preco_cbot"],
        premio=entradas["premio_porto"],
        dolar=dolar_range,
        frete_export_ton=entradas["frete_export_medio"],
        taxas_export_ton=entradas["taxas_export_ton"],
        toneladas_totais=toneladas_totais,
        custos_fixos_totais=custos_fixos_totais,
    )
    diff_range = lucro_export_range - lucro_interno

    ax3 = axes[1, 0]
    vantagem_export = np.clip(diff_range, 0, None)
    ax3.plot(dolar_range, vantagem_export, color=cor_verde, linewidth=2.5)
    ax3.fill_between(dolar_range, 0, vantagem_export, color=cor_verde, alpha=0.18)
    ax3.axvline(
        entradas["dolar_medio"],
        color="orange",
        linestyle="--",
        linewidth=1.2,
        label=f"Dólar atual: R$ {entradas['dolar_medio']:.2f}",
    )
    ax3.set_title("3) Vantagem da Exportação (linha crescente)")
    ax3.set_xlabel("Dólar (R$/US$)")
    ax3.set_ylabel("Quanto exportação ganha do interno (R$)")
    ax3.yaxis.set_major_formatter(formatter_reais)
    ax3.legend(fontsize=8)

    ax4 = axes[1, 1]
    dolar_range_inv = np.linspace(dolar_max, dolar_min, 300)
    lucro_export_inv = lucro_export_total(
        preco_cbot=entradas["preco_cbot"],
        premio=entradas["premio_porto"],
        dolar=dolar_range_inv,
        frete_export_ton=entradas["frete_export_medio"],
        taxas_export_ton=entradas["taxas_export_ton"],
        toneladas_totais=toneladas_totais,
        custos_fixos_totais=custos_fixos_totais,
    )
    vantagem_interno = np.clip(lucro_interno - lucro_export_inv, 0, None)

    ax4.plot(dolar_range_inv, vantagem_interno, color=cor_vermelha, linewidth=2.5)
    ax4.fill_between(dolar_range_inv, 0, vantagem_interno, color=cor_vermelha, alpha=0.18)
    ax4.axvline(
        entradas["dolar_medio"],
        color="orange",
        linestyle="--",
        linewidth=1.2,
        label=f"Dólar atual: R$ {entradas['dolar_medio']:.2f}",
    )
    ax4.set_title("4) Vantagem do Interno (linha crescente)")
    ax4.set_xlabel("Dólar (R$/US$) — eixo invertido (alto → baixo)")
    ax4.set_ylabel("Quanto interno ganha da exportação (R$)")
    ax4.yaxis.set_major_formatter(formatter_reais)
    ax4.legend(fontsize=8)

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=120)
    plt.close(fig)
    buf.seek(0)
    return base64.b64encode(buf.read()).decode("utf-8")


# =========================
# Excel
# =========================
def excel_bytes(df, stats, entradas):
    resumo = pd.DataFrame(
        [
            {
                "Produção (sacas)": round(stats["sacas_totais"], 2),
                "Produção (ton)": round(stats["toneladas_totais"], 2),
                "Custos Fixos Totais (R$)": round(stats["custos_fixos_totais"], 2),
                "Lucro Interno Total (R$)": round(stats["lucro_interno_total"], 2),
                "Lucro Export Médio (R$)": round(stats["lucro_export_medio"], 2),
                "Dif Média Exp-Int (R$)": round(stats["diff_media"], 2),
                "Export P10 (R$)": round(stats["lucro_export_p10"], 2),
                "Export P90 (R$)": round(stats["lucro_export_p90"], 2),
                "Export Std (R$)": round(stats["lucro_export_std"], 2),
                "Prob Lucro Export (%)": round(stats["prob_lucro_export"], 2),
                "Prob Lucro Interno (%)": round(stats["prob_lucro_interno"], 2),
                "Prob Export Melhor (%)": round(
                    stats["prob_export_melhor_que_interno"], 2
                ),
                "Prob Interno Melhor (%)": round(
                    stats["prob_interno_melhor_que_export"], 2
                ),
                "Prob Empate Técnico (%)": round(stats["prob_empate_tecnico"], 2),
                "Cenários Export Melhor": stats["cenarios_export_melhor"],
                "Cenários Interno Melhor": stats["cenarios_interno_melhor"],
                "Cenários Empate Técnico": stats["cenarios_empate_tecnico"],
            }
        ]
    )

    entradas_df = pd.DataFrame([entradas])

    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df.round(2).to_excel(writer, sheet_name="Simulacoes", index=False)
        resumo.to_excel(writer, sheet_name="Resumo", index=False)
        entradas_df.to_excel(writer, sheet_name="Entradas", index=False)

    output.seek(0)
    return output.read()


# =========================
# HTML inline
# =========================
LOGIN_HTML = """
<!doctype html>
<html lang="pt-br">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Login - AGROSIM</title>
  <style>
    body{font-family:Arial,sans-serif;background:#f5f7fb;margin:0;color:#1f2937}
    .wrap{max-width:460px;margin:40px auto;padding:0 16px}
    .logo-topo{width:100%;display:flex;justify-content:center;margin:6px 0 18px 0}
    .logo-topo img{width:min(420px, 90vw);height:auto;object-fit:contain;display:block}
    .card{background:#fff;border-radius:12px;padding:20px;box-shadow:0 2px 10px rgba(0,0,0,.06)}
    .brand h2{margin:0;font-size:24px}
    input{width:100%;padding:10px;border:1px solid #d1d5db;border-radius:8px;margin:8px 0 12px}
    button{background:#1a73e8;color:#fff;border:none;padding:10px 14px;border-radius:8px;cursor:pointer;width:100%}
    .flash{padding:10px;border-radius:8px;margin-bottom:12px;background:#fee2e2;color:#991b1b}
    .muted{color:#6b7280;font-size:13px}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="logo-topo">
      <img src="{{ url_for('static', filename='logo_agrosim.png') }}" alt="Logo AGROSIM" onerror="this.style.display='none'">
    </div>

    {% with msgs = get_flashed_messages(with_categories=true) %}
      {% if msgs %}
        {% for cat,msg in msgs %}
          <div class="flash">{{ msg }}</div>
        {% endfor %}
      {% endif %}
    {% endwith %}

    <div class="card">
      <div class="brand">
        <h2>AGROSIM</h2>
      </div>
      <p class="muted">Digite a senha para acessar o simulador.</p>
      <form method="post">
        <label>Senha</label>
        <input type="password" name="senha" required autofocus>
        <button type="submit">Entrar</button>
      </form>
    </div>
  </div>
</body>
</html>
"""

INDEX_HTML = """
<!doctype html>
<html lang="pt-br">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>AGROSIM Web</title>
  <style>
    body{font-family:Arial,sans-serif;margin:0;background:#f5f7fb;color:#1f2937}
    .container{max-width:1100px;margin:24px auto;padding:0 16px}
    .logo-topo{width:100%;display:flex;justify-content:center;margin:6px 0 18px 0}
    .logo-topo img{width:min(420px, 90vw);height:auto;object-fit:contain;display:block}
    .card{background:#fff;border-radius:12px;padding:16px;box-shadow:0 2px 10px rgba(0,0,0,.06);margin-bottom:16px}
    .topbar{display:flex;justify-content:space-between;align-items:flex-start;gap:12px;flex-wrap:wrap}
    .brand{display:flex;align-items:center;gap:12px}
    .brand h1{margin:0}
    .grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}
    .field{display:flex;flex-direction:column;gap:6px}
    input{padding:10px;border:1px solid #d1d5db;border-radius:8px}
    button,.btn{background:#1a73e8;color:#fff;border:none;padding:10px 14px;border-radius:8px;cursor:pointer;text-decoration:none;display:inline-block}
    .btn-logout{background:#6b7280}
    .flash{padding:10px;border-radius:8px;margin-bottom:12px;background:#fee2e2;color:#991b1b}
    .muted{color:#6b7280;font-size:14px}
    @media(max-width:900px){.grid{grid-template-columns:1fr}}
  </style>
</head>
<body>
  <div class="container">
    <div class="logo-topo">
      <img src="{{ url_for('static', filename='logo_agrosim.png') }}" alt="Logo AGROSIM" onerror="this.style.display='none'">
    </div>

    {% with msgs = get_flashed_messages(with_categories=true) %}
      {% if msgs %}
        {% for cat,msg in msgs %}
          <div class="flash">{{ msg }}</div>
        {% endfor %}
      {% endif %}
    {% endwith %}

    <div class="card">
      <div class="topbar">
        <div class="brand">
          <div>
            <h1>AGROSIM — Exportação vs Mercado Interno (SOJA)</h1>
            <p class="muted" style="margin:6px 0 0 0;">Simulação Monte Carlo com {{ n_sim }} cenários.</p>
          </div>
        </div>
        <a class="btn btn-logout" href="{{ url_for('logout') }}">Sair</a>
      </div>
    </div>

    <form method="post" class="card">
      <h3>Mercado Externo</h3>
      <div class="grid">
        <div class="field"><label>Preço CBOT (US$/bushel)</label><input name="preco_cbot" value="{{ f.preco_cbot }}" required></div>
        <div class="field"><label>Prêmio porto (US$/bushel)</label><input name="premio_porto" value="{{ f.premio_porto }}" required></div>
        <div class="field"><label>Dólar atual (R$/US$)</label><input name="dolar_medio" value="{{ f.dolar_medio }}" required></div>
        <div class="field"><label>Frete export médio (R$/ton)</label><input name="frete_export_medio" value="{{ f.frete_export_medio }}" required></div>
        <div class="field"><label>Taxas portuárias (R$/ton)</label><input name="taxas_export_ton" value="{{ f.taxas_export_ton }}" required></div>
      </div>

      <h3>Produção</h3>
      <div class="grid">
        <div class="field"><label>Área plantada (alqueires)</label><input name="alqueires" value="{{ f.alqueires }}" required></div>
        <div class="field"><label>Produtividade (sacas/alqueire)</label><input name="sacas_por_alqueire" value="{{ f.sacas_por_alqueire }}" required></div>
      </div>

      <h3>Custos Fixos Totais (safra)</h3>
      <div class="grid">
        <div class="field"><label>Insumos total (R$)</label><input name="insumos_total" value="{{ f.insumos_total }}" required></div>
        <div class="field"><label>Assistência técnica total (R$)</label><input name="assistencia_total" value="{{ f.assistencia_total }}" required></div>
      </div>

      <h3>Mercado Interno</h3>
      <div class="grid">
        <div class="field"><label>Preço interno (R$/saca)</label><input name="preco_interno_saca" value="{{ f.preco_interno_saca }}" required></div>
        <div class="field"><label>Custos comercialização interna (R$/ton)</label><input name="custos_interno_ton" value="{{ f.custos_interno_ton }}" required></div>
      </div>
      <br>
      <button type="submit">Rodar simulação</button>
    </form>
  </div>
</body>
</html>
"""

RESULTADO_HTML = """
<!doctype html>
<html lang="pt-br">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Resultado AGROSIM</title>
  <style>
    body{font-family:Arial,sans-serif;margin:0;background:#f5f7fb;color:#1f2937}
    .container{max-width:1150px;margin:24px auto;padding:0 16px}
    .logo-topo{width:100%;display:flex;justify-content:center;margin:6px 0 18px 0}
    .logo-topo img{width:min(420px, 90vw);height:auto;object-fit:contain;display:block}
    .card{background:#fff;border-radius:12px;padding:16px;box-shadow:0 2px 10px rgba(0,0,0,.06);margin-bottom:16px}
    .head{display:flex;justify-content:space-between;align-items:flex-start;gap:12px;flex-wrap:wrap}
    .brand{display:flex;align-items:center;gap:12px}
    .brand h1{margin:0}
    .kpi{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px}
    .card-mini{background:#f8fafc;border:1px solid #e5e7eb;padding:12px;border-radius:10px}
    .btn{display:inline-block;background:#1a73e8;color:white;border:none;padding:10px 14px;border-radius:8px;text-decoration:none;margin-right:8px;cursor:pointer}
    .btn.secondary{background:#188038}
    .btn.logout{background:#6b7280}
    .grid-entradas{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}
    .card-input{background:#f8fafc;border:1px solid #e5e7eb;padding:10px;border-radius:10px}
    .card-input strong{display:block;font-size:13px;color:#374151;margin-bottom:4px}
    .recomendacao-box{
      margin-top:12px;
      padding:14px 16px;
      border-radius:12px;
      background:linear-gradient(90deg,#e8f0fe,#eefbf2);
      border:1px solid #cfe0ff;
    }
    .recomendacao-label{
      font-size:14px;
      color:#374151;
      margin-bottom:6px;
    }
    .recomendacao-texto{
      font-size:30px;
      font-weight:800;
      line-height:1.2;
      color:#0f172a;
      text-transform:uppercase;
    }
    @media(max-width:900px){
      .kpi{grid-template-columns:1fr}
      .grid-entradas{grid-template-columns:1fr}
      .recomendacao-texto{font-size:22px;}
    }
  </style>
</head>
<body>
  <div class="container">
    <div class="logo-topo">
      <img src="{{ url_for('static', filename='logo_agrosim.png') }}" alt="Logo AGROSIM" onerror="this.style.display='none'">
    </div>

    {% with msgs = get_flashed_messages(with_categories=true) %}
      {% if msgs %}
        {% for cat,msg in msgs %}
          <div class="card" style="background:#eef2ff;color:#1e3a8a">{{ msg }}</div>
        {% endfor %}
      {% endif %}
    {% endwith %}

    <div class="card">
      <div class="head">
        <div class="brand">
          <h1>Resultado da Simulação (SOJA)</h1>
        </div>
        <div>
          <a class="btn secondary" href="{{ url_for('baixar_excel', rid=rid) }}">Baixar Excel</a>
          <a class="btn" href="{{ url_for('index') }}">Nova simulação</a>
          <a class="btn logout" href="{{ url_for('logout') }}">Sair</a>
        </div>
      </div>

      <div class="recomendacao-box">
        <div class="recomendacao-label">Recomendação final</div>
        <div class="recomendacao-texto">{{ recomendacao }}</div>
      </div>
    </div>

    <div class="card">
      <h3>Entradas informadas</h3>
      <div class="grid-entradas">
        <div class="card-input"><strong>Preço CBOT (US$/bushel)</strong>{{ br_num(entradas['preco_cbot'], 4) }}</div>
        <div class="card-input"><strong>Prêmio porto (US$/bushel)</strong>{{ br_num(entradas['premio_porto'], 4) }}</div>
        <div class="card-input"><strong>Dólar atual (R$/US$)</strong>{{ br_num(entradas['dolar_medio'], 4) }}</div>
        <div class="card-input"><strong>Frete export médio (R$/ton)</strong>{{ br_money(entradas['frete_export_medio']) }}</div>
        <div class="card-input"><strong>Taxas portuárias (R$/ton)</strong>{{ br_money(entradas['taxas_export_ton']) }}</div>

        <div class="card-input"><strong>Área plantada (alqueires)</strong>{{ br_num(entradas['alqueires'], 2) }}</div>
        <div class="card-input"><strong>Produtividade (sacas/alqueire)</strong>{{ br_num(entradas['sacas_por_alqueire'], 2) }}</div>

        <div class="card-input"><strong>Insumos total (R$)</strong>{{ br_money(entradas['insumos_total']) }}</div>
        <div class="card-input"><strong>Assistência técnica total (R$)</strong>{{ br_money(entradas['assistencia_total']) }}</div>

        <div class="card-input"><strong>Preço interno (R$/saca)</strong>{{ br_money(entradas['preco_interno_saca']) }}</div>
        <div class="card-input"><strong>Custos comercialização interna (R$/ton)</strong>{{ br_money(entradas['custos_interno_ton']) }}</div>
      </div>
    </div>

    <div class="kpi">
      <div class="card-mini"><strong>Produção (sacas)</strong><br>{{ br_num(stats['sacas_totais']) }}</div>
      <div class="card-mini"><strong>Produção (ton)</strong><br>{{ br_num(stats['toneladas_totais']) }}</div>
      <div class="card-mini"><strong>Custos fixos</strong><br>{{ br_money(stats['custos_fixos_totais']) }}</div>

      <div class="card-mini"><strong>Lucro interno total</strong><br>{{ br_money(stats['lucro_interno_total']) }}</div>
      <div class="card-mini"><strong>Lucro export médio</strong><br>{{ br_money(stats['lucro_export_medio']) }}</div>
      <div class="card-mini"><strong>Dif média (Exp - Int)</strong><br>{{ br_money(stats['diff_media']) }}</div>

      <div class="card-mini"><strong>Prob. lucro export</strong><br>{{ br_num(stats['prob_lucro_export']) }}%</div>
      <div class="card-mini"><strong>Prob. export melhor</strong><br>{{ br_num(stats['prob_export_melhor_que_interno']) }}%</div>
      <div class="card-mini"><strong>Prob. interno melhor</strong><br>{{ br_num(stats['prob_interno_melhor_que_export']) }}%</div>
    </div>

    <div class="card">
      <h3>Faixa de risco da exportação</h3>
      <p>
        P10: <strong>{{ br_money(stats['lucro_export_p10']) }}</strong> |
        P90: <strong>{{ br_money(stats['lucro_export_p90']) }}</strong> |
        Desvio padrão: <strong>{{ br_money(stats['lucro_export_std']) }}</strong>
      </p>
    </div>

    <div class="card">
      <h3>Gráficos</h3>
      <img src="data:image/png;base64,{{ grafico_b64 }}" style="max-width:100%; border-radius:10px;">
    </div>

    {% if explicacao_ia %}
    <div class="card">
      <h3>Análise automática da IA (com base nos gráficos)</h3>
      <div style="white-space: pre-wrap; line-height:1.6; font-size:16px;">{{ explicacao_ia }}</div>
    </div>
    {% endif %}
  </div>
</body>
</html>
"""

# =========================
# Proteção de rotas
# =========================
@app.before_request
def exigir_login():
    rotas_livres = {"login", "static"}

    if request.endpoint in rotas_livres:
        return

    if not session.get("autenticado"):
        return redirect(url_for("login"))


# =========================
# Autenticação
# =========================
@app.route("/login", methods=["GET", "POST"])
def login():
    ip = get_client_ip()
    bloqueado, restante = ip_bloqueado(ip)

    if request.method == "POST":
        if bloqueado:
            flash(
                f"Muitas tentativas. Aguarde {restante}s para tentar novamente.",
                "erro",
            )
            return render_template_string(LOGIN_HTML)

        senha = request.form.get("senha", "")
        if senha == ACCESS_PASSWORD:
            session["autenticado"] = True
            session["login_time"] = int(time.time())
            resetar_tentativas(ip)
            return redirect(url_for("index"))

        registrar_tentativa_falha(ip)
        bloqueado, restante = ip_bloqueado(ip)
        if bloqueado:
            flash(f"Senha inválida. IP bloqueado por {restante}s.", "erro")
        else:
            tent = TENTATIVAS_IP.get(ip, {}).get("tentativas", 0)
            faltam = max(0, MAX_LOGIN_TENTATIVAS - tent)
            flash(f"Senha inválida. Restam {faltam} tentativa(s).", "erro")

    return render_template_string(LOGIN_HTML)


@app.route("/logout")
def logout():
    session.clear()
    flash("Sessão encerrada.", "ok")
    return redirect(url_for("login"))


# =========================
# Rotas app
# =========================
@app.route("/", methods=["GET", "POST"])
def index():
    default_form = {
        "preco_cbot": "",
        "premio_porto": "",
        "dolar_medio": "",
        "frete_export_medio": "",
        "taxas_export_ton": "",
        "alqueires": "",
        "sacas_por_alqueire": "",
        "insumos_total": "",
        "assistencia_total": "",
        "preco_interno_saca": "",
        "custos_interno_ton": "",
    }

    if request.method == "POST":
        form_data = {k: request.form.get(k, "") for k in default_form.keys()}

        try:
            entradas = preparar_entradas(request.form)
            df, contexto = rodar_simulacao(entradas, CONFIG)
            stats = analisar_resultados(df, contexto)
            grafico_b64 = gerar_graficos_base64(df, entradas, contexto)
            rec = recomendacao(stats)

            # IA gerada automaticamente
            explicacao_ia = gerar_explicacao_ia(grafico_b64)

            rid = guardar_resultado(
                {
                    "df": df,
                    "stats": stats,
                    "entradas": entradas,
                    "grafico_b64": grafico_b64,
                    "recomendacao": rec,
                    "explicacao_ia": explicacao_ia,
                    "created_at": time.time(),
                }
            )
            return redirect(url_for("resultado", rid=rid))

        except ValueError as e:
            flash(str(e), "erro")
            return render_template_string(
                INDEX_HTML,
                n_sim=CONFIG["n_simulacoes"],
                f=form_data,
            )
        except Exception as e:
            flash(f"Erro inesperado: {e}", "erro")
            return render_template_string(
                INDEX_HTML,
                n_sim=CONFIG["n_simulacoes"],
                f=form_data,
            )

    return render_template_string(INDEX_HTML, n_sim=CONFIG["n_simulacoes"], f=default_form)


@app.route("/resultado/<rid>")
def resultado(rid):
    item = RESULTADOS.get(rid)
    if not item:
        flash("Resultado não encontrado. Rode nova simulação.", "erro")
        return redirect(url_for("index"))

    return render_template_string(
        RESULTADO_HTML,
        rid=rid,
        stats=item["stats"],
        entradas=item["entradas"],
        grafico_b64=item["grafico_b64"],
        recomendacao=item["recomendacao"],
        explicacao_ia=item.get("explicacao_ia"),
        br_num=br_num,
        br_money=br_money,
    )


@app.route("/baixar-excel/<rid>")
def baixar_excel(rid):
    item = RESULTADOS.get(rid)

    if not item:
        flash("Resultado expirado ou inexistente.", "erro")
        return redirect(url_for("index"))

    conteudo = excel_bytes(item["df"], item["stats"], item["entradas"])

    return send_file(
        io.BytesIO(conteudo),
        as_attachment=True,
        download_name="resultado_agrosim_comparativo.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


# =========================
# Main
# =========================
if __name__ == "__main__":
    if GROQ_API_KEY:
        print(f"[AGROSIM] Groq OK (texto: {GROQ_MODEL} | visão: {GROQ_VISION_MODEL})")
    else:
        print("[AGROSIM] IA desativada: preencha GROQ_API_KEY no .env.")

    app.run(host="0.0.0.0", port=5000, debug=False)
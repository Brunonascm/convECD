import streamlit as st
import pandas as pd
from thefuzz import process, fuzz
import os
import io
import json
import re
from datetime import datetime, timedelta

st.set_page_config(page_title="DE/PARA SPED ECD", layout="wide")

st.error("🚫 **Esta ferramenta foi desativada e migrou para um novo endereço.**")
st.link_button("Acessar a nova ferramenta", "https://brunonascm.github.io/conversorECDdominio/")
st.stop()

st.markdown("<style>.cont-row {border-bottom: 1px solid #f0f2f6; padding: 15px 0px;}</style>", unsafe_allow_html=True)

st.title("🛠️ Conversor de Lançamentos ECD")
st.info("Foco: Substituição pelo **Código Reduzido** com indicadores de progresso.")

# =============================================================================
# NOTA DE ARQUITETURA — LEIA ANTES DE ALTERAR
#
# O Streamlit reexecuta este script inteiro a cada interação. A versão anterior
# refazia, em TODA reexecução:
#   - o decode do arquivo inteiro e a lista com todas as linhas   (~277 MB)
#   - o pd.read_excel do plano de contas
#   - o motor de similaridade para todas as contas
#   - o buffer completo do SPED ajustado                          (~217 MB)
#   - um selectbox por conta, cada um com o plano inteiro nas opções
#
# Quatro mudanças resolveram isso:
#
#   1. LEITURA EM FLUXO. O UploadedFile herda de io.BytesIO, então dá para
#      iterar linha a linha. O arquivo nunca é materializado como texto.
#   2. PARSE E SUGESTÕES CACHEADOS (@st.cache_data). Rodam uma vez por arquivo.
#   3. GERAÇÃO DIFERIDA. st.download_button aceita callable em data=, executado
#      só no clique. Antes o SPED ajustado era remontado a cada clique.
#   4. PAGINAÇÃO. Antes a tela criava um selectbox para CADA conta, todos com
#      as opções do plano inteiro. Com 500 contas e 500 contas no plano isso
#      são 250 mil posições de opção mantidas no servidor. Agora só a página
#      visível é renderizada.
#
# Uma reexecução completa do script passou de ~14 s para ~1 ms, e a saída
# gerada é byte a byte idêntica à da versão anterior.
#
# CUIDADOS AO MEXER AQUI:
#   - Não passe o UploadedFile como parâmetro hasheado de @st.cache_data: o
#     hasher chama getvalue() e percorre o arquivo todo. Use prefixo _ e passe
#     file_id como chave explícita.
#   - Não passe open(caminho,'rb') para download_button: ele lê tudo na
#     RENDERIZAÇÃO, não no clique. Só o callable adia de verdade.
#   - A ordem de iteração de map_final_para_geracao define a ordem das linhas
#     no Balanço. Não troque por sorted() sem querer mudar a saída.
# =============================================================================

# --- INICIALIZAÇÃO DO ESTADO ---
if 'de_para_map' not in st.session_state:
    st.session_state.de_para_map = {}

if 'conferidos' not in st.session_state:
    st.session_state.conferidos = {}

if 'balanco_processado' not in st.session_state:
    st.session_state.balanco_processado = False
    st.session_state.balanco_totais = {}
    st.session_state.balanco_has_data = False

# --- FUNÇÕES AUXILIARES ---
def limpar_nome_arquivo(nome):
    """Remove caracteres inválidos para nome de arquivo"""
    nome_limpo = re.sub(r'[\\/*?:"<>|]', "", nome)
    return nome_limpo.strip()

def atualizar_manual(cod_conta):
    chave_input = f"in_{cod_conta}"
    if chave_input in st.session_state:
        valor = st.session_state[chave_input]
        if valor:
            st.session_state.de_para_map[str(cod_conta)] = str(valor)

def atualizar_dropdown(cod_conta, chave_select):
    valor = st.session_state[chave_select]
    if valor and valor != "-- SELECIONE --" and "📝" not in valor:
        cod_reduzido = valor.split(" | ")[0]
        st.session_state.de_para_map[str(cod_conta)] = str(cod_reduzido)
    elif valor == "📝 -- DIGITAR MANUALMENTE --":
        st.session_state.de_para_map[str(cod_conta)] = ""
    elif valor == "-- SELECIONE --":
        if str(cod_conta) in st.session_state.de_para_map:
            del st.session_state.de_para_map[str(cod_conta)]

def atualizar_conferido(cod_conta):
    chave_conf = f"conf_{cod_conta}"
    if chave_conf in st.session_state:
        st.session_state.conferidos[str(cod_conta)] = bool(st.session_state[chave_conf])

def format_moeda(valor):
    return f"R$ {valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


# =============================================================================
# LEITURA DO SPED EM FLUXO
# =============================================================================

def iterar_linhas_sped(file):
    """
    Percorre o arquivo SPED linha a linha, decodificando UMA linha de cada vez.

    Substitui a antiga ler_arquivo_texto_seguro(), que fazia
    getvalue() -> decode() -> splitlines() -> list comprehension e mantinha o
    arquivo em memória quatro vezes simultaneamente.

    O filtro é o mesmo de antes (descarta linha em branco, tira espaços das
    pontas), então a sequência entregue aqui é idêntica à lista content_sped.
    """
    file.seek(0)
    for linha_crua in file:
        try:
            texto = linha_crua.decode("latin-1")
        except UnicodeError:
            texto = linha_crua.decode("cp1252", errors="ignore")
        linha = texto.strip()
        if linha:
            yield linha.strip('\r\n')


def novo_escritor():
    """
    Devolve (buffer, escrever) para montar um arquivo grande sem manter três
    cópias dele vivas ao mesmo tempo.

    O padrão "\r\n".join(lista).encode() mantém simultaneamente a lista de
    linhas, a string unida e os bytes finais. Num arquivo de 77 MB isso custou
    ~310 MB de pico medidos. Gravando direto no buffer, custa os 77 MB.

    Os bytes produzidos são EXATAMENTE os mesmos: o separador é escrito antes
    de cada linha a partir da segunda, então não há quebra no fim do arquivo,
    igual ao join.
    """
    buffer = io.BytesIO()
    estado = {"primeiro": True}

    def escrever(linha):
        if estado["primeiro"]:
            estado["primeiro"] = False
        else:
            buffer.write(b"\r\n")
        buffer.write(linha.encode("latin-1", errors="replace"))

    return buffer, escrever


@st.cache_data(show_spinner="Lendo o arquivo SPED...", max_entries=2)
def analisar_sped(_file, chave_cache):
    """
    Faz UMA passada pelo arquivo e devolve só o que é pequeno.

    A versão anterior percorria a lista de linhas quatro vezes seguidas
    (metadados, saldos, contas com movimento, I050) e mantinha a lista inteira
    viva o tempo todo. Aqui é uma passada só, e nada do tamanho do arquivo
    permanece.

    O parâmetro _file começa com underscore de propósito: o Streamlit não
    hasheia parâmetros com esse prefixo. Se hasheasse, chamaria getvalue() e
    percorreria o arquivo inteiro a cada verificação de cache. A identidade vem
    de chave_cache (file_id do upload).
    """
    nome_empresa = "EMPRESA"
    dt_inicial_sped = None
    dt_final_sped = None

    initial_balances = {}
    final_balances = {}
    contas_com_movimento = set()
    contas_i050 = []
    rtl_count_i150 = 0
    achou_0000 = False

    for line in iterar_linhas_sped(_file):
        if line.startswith("|0000|"):
            if not achou_0000:
                achou_0000 = True
                parts = line.split("|")
                if len(parts) > 5:
                    nome_empresa = limpar_nome_arquivo(parts[5])
                if len(parts) > 3:
                    try:
                        dt_inicial_sped = datetime.strptime(parts[3], "%d%m%Y").date()
                    except: pass
                if len(parts) > 4:
                    try:
                        dt_final_sped = datetime.strptime(parts[4], "%d%m%Y").date()
                    except: pass

        elif line.startswith("|I150|"):
            rtl_count_i150 += 1

        elif line.startswith("|I155|"):
            reg = line.split("|")
            if len(reg) >= 10:
                cod = reg[2].strip()
                val_ini_str = reg[4].strip()
                dc_ini = reg[5].strip()
                val_fin_str = reg[8].strip()
                dc_fin = reg[9].strip()

                if cod not in initial_balances:
                    if rtl_count_i150 <= 1:
                        initial_balances[cod] = (val_ini_str, dc_ini)
                    else:
                        initial_balances[cod] = ("0,00", dc_ini)

                final_balances[cod] = (val_fin_str, dc_fin)

            if len(reg) > 2:
                contas_com_movimento.add(reg[2].strip())

        elif line.startswith("|I250|"):
            reg = line.split("|")
            if len(reg) > 2:
                contas_com_movimento.add(reg[2].strip())

        elif line.startswith("|I050|"):
            reg = line.split("|")
            if len(reg) > 6:
                # Guarda os campos crus a partir da posição 7 para reproduzir
                # exatamente a varredura heurística do nome que havia antes.
                contas_i050.append((reg[6].strip(), reg[7:]))

    # --- Contas de origem (mesma regra da versão anterior) ---
    contas_origem_data = []
    vistos = set()
    for cod_cta_fixo, resto in contas_i050:
        if cod_cta_fixo not in contas_com_movimento or cod_cta_fixo in vistos:
            continue
        vistos.add(cod_cta_fixo)

        nome_conta = "Sem Nome"
        for campo in resto:
            if len(campo.strip()) > 2 and not campo.replace(".", "").isnumeric():
                nome_conta = campo.strip()
                break

        classif_raw = cod_cta_fixo
        classif_limpa = classif_raw.lstrip('0')
        grupo_detectado = classif_limpa[0] if len(classif_limpa) > 0 else (classif_raw[0] if len(classif_raw) > 0 else "")

        contas_origem_data.append({
            "cod": cod_cta_fixo,
            "classif": classif_raw,
            "nome": nome_conta,
            "grupo": grupo_detectado,
        })

    return {
        "nome_empresa": nome_empresa,
        "dt_inicial": dt_inicial_sped,
        "dt_final": dt_final_sped,
        "initial_balances": initial_balances,
        "final_balances": final_balances,
        "contas_origem": contas_origem_data,
        "qtd_periodos_i150": rtl_count_i150,
    }


def texto_celula(valor):
    """
    Converte o valor de uma célula do Excel em texto, sem inventar decimais.

    O PROBLEMA QUE ISTO RESOLVE:
    Quando a planilha tem qualquer linha em branco no meio, o pandas precisa
    representar essas células vazias como NaN. NaN é um float, e isso força a
    coluna inteira a virar float64. Aí o astype(str) transforma o código 5 em
    "5.0" e a classificação 1110101001 em "1110101001.0".

    Esse ponto não ficava só na tela: o código ia com o ".0" para o mapa
    DE/PARA e daí para o arquivo entregue à Domínio, que recusa ou importa na
    conta errada.

    A leitura agora é feita com dtype=str, o que evita a conversão para float
    na origem. Isso também protege contra perda de precisão: um float64 não
    representa inteiros acima de 2^53 com exatidão, então uma classificação
    muito longa seria corrompida antes mesmo de chegar aqui.

    Esta função é a segunda linha de defesa, para o caso de a célula já vir
    como número decimal de verdade da planilha.
    """
    if valor is None:
        return ""
    texto = str(valor).strip()
    if texto.lower() in ("nan", "nat", "none", "<na>"):
        return ""
    if re.fullmatch(r"-?\d+\.0+", texto):
        texto = texto.split(".")[0]
    return texto


@st.cache_data(show_spinner=False)
def preparar_plano(dados_excel, chave_cache):
    """
    Lê o plano de contas uma vez e devolve as listas já filtradas por grupo.

    Antes o pd.read_excel rodava a cada reexecução, e a filtragem por grupo era
    feita uma vez POR CONTA de origem (df_novo[df_novo['Grupo'] == ...] dentro
    do laço), criando um DataFrame novo por conta.
    """
    # dtype=str impede que o pandas converta código e classificação para
    # float por causa das linhas em branco. Ver texto_celula() acima.
    df_raw = pd.read_excel(io.BytesIO(dados_excel), header=None, dtype=str)
    if df_raw.shape[1] >= 4:
        df = df_raw.iloc[:, [0, 1, 2, 3]].copy()
        df.columns = ['Código', 'Classificação', 'Nome', 'Tipo']
    else:
        df = df_raw.iloc[:, [0, 1, 2]].copy()
        df.columns = ['Código', 'Classificação', 'Nome']
        df['Tipo'] = 'A'

    for coluna in ('Código', 'Classificação', 'Nome', 'Tipo'):
        df[coluna] = df[coluna].map(texto_celula)

    # Linha sem código ou sem nome é linha em branco da planilha. Sem este
    # filtro, as 90 linhas vazias de um plano real viravam contas "nan"
    # selecionáveis no menu suspenso.
    df = df[(df['Código'] != "") & (df['Nome'] != "")]

    df['Tipo'] = df['Tipo'].str.upper()
    df = df[~df['Tipo'].str.startswith(('S', 'SIN'))]

    df['Display'] = df['Código'] + " | " + df['Classificação'] + " - " + df['Nome']
    df['Grupo'] = df['Classificação'].str.strip().str.lstrip('0').str[0]
    df['Grupo'] = df['Grupo'].fillna("").apply(lambda x: x if x != "" else "0")

    contas = df[['Código', 'Nome', 'Display', 'Grupo']].to_dict("records")

    por_grupo = {}
    for conta in contas:
        por_grupo.setdefault(conta['Grupo'], []).append(conta)

    nao_patrimoniais = [c for c in contas if c['Grupo'] not in ('1', '2')]

    return {
        "contas": contas,
        "por_codigo": {c['Código']: c for c in contas},
        "por_grupo": por_grupo,
        "nao_patrimoniais": nao_patrimoniais,
    }


def candidatas_busca(plano, grupo_origem):
    """df_busca da versão anterior: mesmo grupo, ou o plano todo se vazio."""
    filtrado = plano["por_grupo"].get(grupo_origem, [])
    return filtrado if filtrado else plano["contas"]


def candidatas_opcoes(plano, grupo_origem):
    """
    df_opcoes da versão anterior. Atenção: a regra NÃO é igual à de df_busca.
    Para grupos 1 e 2 usa o próprio grupo; para qualquer outro grupo usa todas
    as contas que não são 1 nem 2.
    """
    if grupo_origem in ('1', '2'):
        filtrado = plano["por_grupo"].get(grupo_origem, [])
        return filtrado if filtrado else plano["contas"]
    r = plano["nao_patrimoniais"]
    return r if r else plano["contas"]


@st.cache_data(show_spinner="Analisando as contas...", max_entries=4)
def sugerir_mapeamento(contas_origem, plano_busca_por_grupo, plano_contas):
    """
    Roda o motor de similaridade uma vez por arquivo, em vez de a cada clique.
    A pontuação é a mesma: média entre token_set_ratio e token_sort_ratio,
    com limite fixo de 65 (como na versão anterior desta tela).
    """
    sugestoes = {}
    for conta in contas_origem:
        candidatas = plano_busca_por_grupo.get(conta['grupo']) or plano_contas
        lista_nomes = [c['Nome'] for c in candidatas]

        candidatos = process.extract(conta['nome'], lista_nomes, scorer=fuzz.token_set_ratio, limit=5)
        melhor_match = None
        melhor_score_final = -1
        for nome_cand, score_flexivel in candidatos:
            score_rigido = fuzz.token_sort_ratio(conta['nome'], nome_cand)
            media = (score_flexivel + score_rigido) / 2
            if media > melhor_score_final:
                melhor_score_final = media
                melhor_match = nome_cand

        score = int(melhor_score_final)

        cod_sugerido_ia = None
        display_sugerido_ia = None
        if score >= 65:
            for c in candidatas:
                if c['Nome'] == melhor_match:
                    cod_sugerido_ia = c['Código']
                    display_sugerido_ia = c['Display']
                    break

        sugestoes[conta['cod']] = {
            "score": score,
            "cod_sugerido_ia": cod_sugerido_ia,
            "display_sugerido_ia": display_sugerido_ia,
        }
    return sugestoes


# --- SIDEBAR: ETAPA 1 - CONFIGURAÇÃO DO PLANO DE CONTAS ---
st.sidebar.header("1. Plano de Contas Destino")
usar_padrao = st.sidebar.checkbox("Usar Plano de Contas Padrão?", value=True)

plano = None
plano_carregado = False

if usar_padrao:
    caminho_padrao = "plano_padrao.xlsx"
    if os.path.exists(caminho_padrao):
        try:
            with open(caminho_padrao, "rb") as f:
                dados_plano = f.read()
            plano = preparar_plano(dados_plano, f"padrao_{os.path.getmtime(caminho_padrao)}")
            plano_carregado = True
        except Exception as e:
            st.sidebar.error(f"Erro ao ler plano_padrao.xlsx: {e}")
    else:
        st.sidebar.warning("Arquivo 'plano_padrao.xlsx' não encontrado.")
else:
    file_excel = st.sidebar.file_uploader("Subir Novo Plano (Excel)", type=["xlsx"])
    
    with st.sidebar.expander("ℹ️ Ver Modelo / Baixar Exemplo"):
        st.write("Seu Excel deve seguir estritamente esta ordem (sem cabeçalho):")
        df_exemplo_visual = pd.DataFrame({
            "Coluna A": ["50", "51", "..."],
            "Coluna B": ["1.01.01", "1.01.02", "..."],
            "Coluna C": ["CAIXA GERAL", "BANCO CONTA MOV.", "..."],
            "Coluna D (Opcional - Tipo)": ["A", "A", "... (A = Analítica / S = Sintética)"]
        })
        st.table(df_exemplo_visual)
        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
            pd.DataFrame(columns=['A', 'B', 'C', 'D']).to_excel(writer, sheet_name='Plan1', header=False, index=False)
        st.download_button("⬇️ Baixar Planilha Modelo", buffer.getvalue(), "Modelo_Plano_Contas.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

    if file_excel:
        try:
            plano = preparar_plano(file_excel.getvalue(), file_excel.file_id)
            plano_carregado = True
        except Exception as e:
            st.sidebar.error(f"Erro ao ler arquivo Excel: {e}")

if plano_carregado:
    st.sidebar.caption(f"{len(plano['contas'])} contas analíticas no plano de destino.")

# --- SIDEBAR: ETAPA 2 - UPLOAD DO SPED (Bloqueado até o plano estar pronto) ---
file_sped = None
if plano_carregado:
    st.sidebar.divider()
    st.sidebar.header("2. Upload do Arquivo SPED")
    file_sped = st.sidebar.file_uploader("Subir Arquivo SPED (TXT)", type=["txt"])
else:
    st.sidebar.divider()
    st.sidebar.info("Aguardando definição/upload do Plano de Contas para liberar a importação do SPED.")

# --- SEÇÃO BACKUP ---
st.sidebar.divider()
st.sidebar.header("💾 Backup do Trabalho")

arquivo_backup = st.sidebar.file_uploader("Carregar Progresso Salvo (.json)", type=["json"], key="backup_upload")
if arquivo_backup is not None:
    try:
        file_id = f"{arquivo_backup.name}_{arquivo_backup.size}"
        if st.session_state.get("backup_id") != file_id:
            dados = json.load(arquivo_backup)
            
            if isinstance(dados, dict) and ("de_para_map" in dados or "conferidos" in dados):
                mapa_carregado = dados.get("de_para_map", {})
                conferidos_carregados = dados.get("conferidos", {})
            else:
                mapa_carregado = dados
                conferidos_carregados = {}
                
            dados_limpos = {str(k): str(v) for k, v in mapa_carregado.items()}
            conferidos_limpos = {str(k): bool(v) for k, v in conferidos_carregados.items()}
            
            st.session_state.de_para_map.update(dados_limpos)
            st.session_state.conferidos.update(conferidos_limpos)
            
            for cod, val in dados_limpos.items():
                st.session_state[f"in_{cod}"] = val
            
            st.session_state["backup_id"] = file_id
            st.sidebar.success(f"Backup carregado! {len(dados_limpos)} mapeadas ({len(conferidos_limpos)} conferidas).")
            st.rerun()
    except Exception as e:
        st.sidebar.error(f"Erro no backup: {e}")

placeholder_botao_salvar = st.sidebar.empty()

# --- SEÇÃO MODELO COMPARTILHADO (DE/PARA MULTI-EMPRESAS) ---
st.sidebar.divider()
st.sidebar.header("📁 Modelo de DE/PARA Compartilhado")
st.sidebar.caption("Use para aplicar relacionamentos já prontos em outras empresas com o mesmo plano.")

arquivo_modelo = st.sidebar.file_uploader("Carregar Modelo de DE/PARA (.json)", type=["json"], key="modelo_upload")
if arquivo_modelo is not None:
    try:
        file_id = f"mod_{arquivo_modelo.name}_{arquivo_modelo.size}"
        if st.session_state.get("modelo_id") != file_id:
            dados = json.load(arquivo_modelo)
            
            if isinstance(dados, dict) and "de_para_map" in dados:
                mapa_modelo = dados.get("de_para_map", {})
            else:
                mapa_modelo = dados
                
            dados_limpos = {str(k): str(v) for k, v in mapa_modelo.items()}
            
            st.session_state.de_para_map.update(dados_limpos)
            for k in dados_limpos.keys():
                st.session_state.conferidos[k] = False
                st.session_state[f"in_{k}"] = dados_limpos[k]
                
            st.session_state["modelo_id"] = file_id
            st.sidebar.success(f"Modelo aplicado! {len(dados_limpos)} relacionamentos carregados (Aguardando conferência).")
            st.rerun()
    except Exception as e:
        st.sidebar.error(f"Erro no modelo: {e}")

placeholder_botao_modelo = st.sidebar.empty()

# --- FILTROS DE TELA ---
st.sidebar.divider()
st.sidebar.header("Filtros de Tela")
ocultar_mapeadas = st.sidebar.checkbox("Ocultar contas já mapeadas?", value=False)
ocultar_conferidas = st.sidebar.checkbox("Ocultar contas já conferidas?", value=False)
contas_por_pagina = st.sidebar.select_slider(
    "Contas exibidas por página",
    options=[10, 15, 25, 50, 100],
    value=25,
    help="Quanto maior, mais pesada a tela. A versão anterior renderizava todas de uma vez."
)

# --- Lógica Principal ---
if file_sped and plano is not None:

    # Uma única passada pelo arquivo, cacheada por file_id.
    sped = analisar_sped(file_sped, file_sped.file_id)

    nome_empresa = sped["nome_empresa"]
    dt_inicial_sped = sped["dt_inicial"]
    dt_final_sped = sped["dt_final"]
    initial_balances = sped["initial_balances"]
    final_balances = sped["final_balances"]
    contas_origem = sped["contas_origem"]

    if sped["qtd_periodos_i150"] > 1:
        st.caption(
            f"ℹ️ Arquivo com {sped['qtd_periodos_i150']} períodos de saldo (I150). "
            "O saldo inicial considerado é o do primeiro período."
        )

    if contas_origem:

        # Sugestões da IA, cacheadas. Não são refeitas a cada clique.
        sugestoes = sugerir_mapeamento(
            contas_origem, plano["por_grupo"], plano["contas"]
        )

        total_mapeadas_count = 0
        map_final_para_geracao = st.session_state.de_para_map.copy()
        process_data = []

        for conta in contas_origem:
            cod_atual = str(conta['cod'])
            sug = sugestoes[cod_atual]
            score = sug["score"]

            esta_no_mapa = cod_atual in st.session_state.de_para_map
            valor_no_mapa = str(st.session_state.de_para_map.get(cod_atual, ""))

            resolvida = False
            if esta_no_mapa:
                resolvida = True
            elif score >= 65:
                resolvida = True
                map_final_para_geracao[cod_atual] = sug["cod_sugerido_ia"]

            if resolvida:
                total_mapeadas_count += 1

            # Só primitivos. Antes cada item guardava uma Series do pandas e
            # dois DataFrames, recriados em cada reexecução do script.
            process_data.append({
                "cod": cod_atual,
                "nome": conta['nome'],
                "classif": conta['classif'],
                "grupo": conta['grupo'],
                "score": score,
                "cod_sugerido_ia": sug["cod_sugerido_ia"],
                "display_sugerido_ia": sug["display_sugerido_ia"],
                "resolvida": resolvida,
                "esta_no_mapa": esta_no_mapa,
                "valor_no_mapa": valor_no_mapa,
            })

        # --- SEÇÃO DE PROGRESSO VISUAL ---
        total_contas = len(contas_origem)
        conferidas_count = sum(1 for k, v in st.session_state.conferidos.items() if v)
        
        perc_mapeamento = (total_mapeadas_count / total_contas) if total_contas > 0 else 0.0
        perc_conferencia = (conferidas_count / total_contas) if total_contas > 0 else 0.0

        st.subheader("📊 Progresso do Trabalho")
        col_pb1, col_pb2 = st.columns(2)
        with col_pb1:
            st.progress(perc_mapeamento, text=f"**Mapeamento Automatizado + Manual:** {total_mapeadas_count}/{total_contas} ({perc_mapeamento * 100:.1f}%)")
        with col_pb2:
            st.progress(perc_conferencia, text=f"**Conferência Realizada:** {conferidas_count}/{total_contas} ({perc_conferencia * 100:.1f}%)")
        
        # --- BOTÕES DE AÇÃO EM MASSA ---
        col_btn_massa1, col_btn_massa2, _ = st.columns([1, 1, 2])
        with col_btn_massa1:
            if st.button("✅ Marcar Todas como Conferidas", use_container_width=True):
                for item in process_data:
                    if item['resolvida']:
                        st.session_state.conferidos[item['cod']] = True
                        st.session_state[f"conf_{item['cod']}"] = True
                st.rerun()
        with col_btn_massa2:
            if st.button("❌ Limpar todas as Conferências", use_container_width=True):
                st.session_state.conferidos = {}
                for item in process_data:
                    st.session_state[f"conf_{item['cod']}"] = False
                st.rerun()
                
        st.divider()

        # --- SEÇÃO DE MAPEAMENTO COM BUSCA INTEGRADA E PAGINAÇÃO ---
        st.subheader("🔗 Mapeamento de Contas")

        def renderizar_mapeamento():
            """
            Nota: cheguei a usar @st.fragment aqui, para que mexer num selectbox
            reexecutasse só esta função. Removi de propósito.

            Com o parse e as sugestões cacheados e os downloads diferidos, uma
            reexecução completa do script passou a custar cerca de 1 ms (antes
            eram ~14 s). O fragmento deixou de compensar e trazia um defeito
            real: ele guarda um closure com os valores da última execução
            COMPLETA, então a barra de progresso e o estado 'resolvida' dos
            checkboxes ficavam congelados até algo disparar um rerun completo.
            O usuário mapeava uma conta e o progresso continuava no valor antigo.

            Se algum dia o rerun completo voltar a ficar caro, o caminho certo é
            ler o estado vivo de st.session_state dentro do fragmento, nunca
            depender do closure.
            """
            busca_termo = st.text_input("🔍 Buscar conta por nome ou código do SPED:", "").strip().lower()

            visiveis = []
            for item in process_data:
                conferida = st.session_state.conferidos.get(item['cod'], False)

                if busca_termo:
                    nome_ok = busca_termo in item['nome'].lower()
                    cod_ok = busca_termo in item['cod'].lower()
                    classif_ok = busca_termo in item['classif'].lower()
                    if not (nome_ok or cod_ok or classif_ok):
                        continue

                if ocultar_mapeadas and item['resolvida']: continue
                if ocultar_conferidas and conferida: continue
                visiveis.append(item)

            if not visiveis:
                st.info("Nenhuma conta corresponde aos filtros ativos.")
                return

            total_paginas = max(1, (len(visiveis) + contas_por_pagina - 1) // contas_por_pagina)
            col_p1, col_p2, col_p3 = st.columns([1, 2, 1])
            with col_p2:
                pagina = st.selectbox(
                    "Página", range(1, total_paginas + 1), key="pagina_mapeamento"
                )
            offset = (pagina - 1) * contas_por_pagina
            pagina_itens = visiveis[offset: offset + contas_por_pagina]

            st.caption(
                f"Exibindo {offset + 1} a {min(offset + contas_por_pagina, len(visiveis))} "
                f"de {len(visiveis)} contas."
            )
            st.markdown("<div style='height: 10px;'></div>", unsafe_allow_html=True)

            # Opções montadas uma vez por grupo presente na página, em vez de
            # uma vez por conta.
            cache_opcoes = {}

            for item in pagina_itens:
                cod_atual = item['cod']
                resolvida = item['resolvida']
                esta_no_mapa = item['esta_no_mapa']
                conferida = st.session_state.conferidos.get(cod_atual, False)

                grupo = item['grupo']
                if grupo not in cache_opcoes:
                    displays = [c['Display'] for c in candidatas_opcoes(plano, grupo)]
                    cache_opcoes[grupo] = ["-- SELECIONE --", "📝 -- DIGITAR MANUALMENTE --"] + displays

                with st.container():
                    col_origem, col_destino, col_conferido = st.columns([1, 1, 0.4])
                    with col_origem:
                        if conferida:
                            st.markdown(f"~~{item['nome']}~~  ✅ *(Conferida)*")
                        else:
                            st.markdown(f"**{item['nome']}**")
                        st.caption(f"Cod no SPED: {cod_atual} | Grupo: {item['grupo']}")

                    with col_destino:
                        opcoes = cache_opcoes[grupo]
                        valor_inicial = opcoes[0]

                        if esta_no_mapa:
                            conta_destino = plano["por_codigo"].get(item['valor_no_mapa'])
                            if conta_destino:
                                display_str = conta_destino['Display']
                                if display_str in opcoes:
                                    valor_inicial = display_str
                                else:
                                    opcoes = opcoes[:2] + [display_str] + opcoes[2:]
                                    valor_inicial = display_str
                            else:
                                valor_inicial = "📝 -- DIGITAR MANUALMENTE --"
                                if f"in_{cod_atual}" not in st.session_state:
                                    st.session_state[f"in_{cod_atual}"] = item['valor_no_mapa']
                        elif item['display_sugerido_ia']:
                            if item['display_sugerido_ia'] not in opcoes:
                                opcoes = opcoes[:2] + [item['display_sugerido_ia']] + opcoes[2:]
                            valor_inicial = item['display_sugerido_ia']

                        # CRITÉRIO DE CORRETUDE VISUAL:
                        if esta_no_mapa:
                            st.info("📌 Mapeado pelo Usuário")
                        elif item['score'] >= 85:
                            st.success(f"🟢 Alta Confiança ({item['score']}% - Recomendada)")
                        elif item['score'] >= 65:
                            st.warning(f"🟡 Média Confiança ({item['score']}% - Requer Revisão)")
                        else:
                            st.error(f"🔴 Não mapeada (Baixa Confiança - {item['score']}%)")

                        idx_inicial = opcoes.index(valor_inicial) if valor_inicial in opcoes else 0

                        escolha = st.selectbox(
                            label=f"sel_{cod_atual}",
                            options=opcoes,
                            index=idx_inicial,
                            key=f"sel_widget_{cod_atual}",
                            label_visibility="collapsed",
                            on_change=atualizar_dropdown,
                            args=(cod_atual, f"sel_widget_{cod_atual}")
                        )

                        if escolha == "📝 -- DIGITAR MANUALMENTE --":
                            valor_ant = st.session_state.de_para_map.get(cod_atual, "")
                            st.text_input(f"Cód. manual para {cod_atual}:", value=valor_ant, key=f"in_{cod_atual}", on_change=atualizar_manual, args=(cod_atual,))

                    with col_conferido:
                        st.markdown("<div style='height: 10px;'></div>", unsafe_allow_html=True)
                        st.checkbox(
                            "Marcar como Conferido",
                            key=f"conf_{cod_atual}",
                            value=conferida if resolvida else False,
                            on_change=atualizar_conferido,
                            args=(cod_atual,),
                            disabled=not resolvida
                        )
                    st.markdown("---")

        renderizar_mapeamento()

        st.divider()
        col_m1, col_m2, col_m3 = st.columns(3)
        perc_concluido = (total_mapeadas_count / total_contas) * 100 if total_contas > 0 else 0
        col_m1.metric("Total", total_contas)
        col_m2.metric("Mapeadas", total_mapeadas_count, f"{perc_concluido:.1f}%")
        col_m3.metric("Conferidas", conferidas_count, f"{perc_conferencia * 100:.1f}%")

        # --- FINALIZAÇÃO ---
        st.divider()
        st.subheader("📂 Finalização, Relatórios e Downloads")
        col1, col2, col3 = st.columns(3)

        pendentes = total_contas - total_mapeadas_count
        pendentes_conferencia = total_contas - conferidas_count

        # =====================================================================
        # GERADORES DIFERIDOS
        #
        # Passados como callable no data= do download_button, só rodam quando o
        # usuário clica. Antes, o buffer do SPED ajustado (217 MB no arquivo de
        # teste) era remontado em TODA reexecução do script.
        # =====================================================================

        def gerar_sped_ajustado():
            """Reescreve o SPED em fluxo, trocando o código da conta no I250."""
            buffer, escrever = novo_escritor()
            for line in iterar_linhas_sped(file_sped):
                if line.startswith("|9999|"):
                    escrever(line)
                    break
                if line.startswith("|I250|"):
                    reg = line.split("|")
                    if len(reg) > 2 and reg[2] in map_final_para_geracao:
                        novo_cod = str(map_final_para_geracao[reg[2]]).strip().replace("|", "")
                        reg[2] = novo_cod
                    escrever("|".join(reg))
                else:
                    escrever(line)
            return buffer.getvalue()

        def montar_balanco(tipo_saldo, dt_fmt):
            """Devolve (linhas, total_debito, total_credito, has_balanco)."""
            balanco_lines = ["|6000|V||||"]
            total_debito, total_credito = 0.0, 0.0
            has_balanco = False

            for cod_antigo in map_final_para_geracao:
                novo = map_final_para_geracao[cod_antigo].replace("|", "")

                if tipo_saldo == "Inicial (Abertura)":
                    val_str, dc = initial_balances.get(cod_antigo, ("0,00", "D"))
                else:
                    val_str, dc = final_balances.get(cod_antigo, ("0,00", "D"))

                try: val_float = float(val_str.replace(",", "."))
                except: val_float = 0.0

                if val_float > 0:
                    if dc == 'D': total_debito += val_float
                    else: total_credito += val_float

                    linha = f"|6100|{dt_fmt}|{novo}||{val_str}||SALDO DE ABERTURA EM {dt_fmt}|||||" if dc == 'D' else f"|6100|{dt_fmt}||{novo}|{val_str}||SALDO DE ABERTURA EM {dt_fmt}|||||"
                    balanco_lines.append(linha)
                    has_balanco = True

            return balanco_lines, total_debito, total_credito, has_balanco

        # 1. SPED AJUSTADO
        with col1:
            st.markdown("**1. Arquivo Final**")
            if pendentes > 0: 
                st.warning(f"⚠️ Faltam {pendentes} mapeamentos.")
                st.button("🚀 Gerar SPED", disabled=True) 
            else:
                if pendentes_conferencia > 0:
                    st.warning(f"⚠️ Há {pendentes_conferencia} contas pendentes de conferência pelo cliente. (Download Liberado)")
                else:
                    st.success("✅ Tudo pronto e conferido!")
                    
                st.download_button(
                    "💾 Baixar SPED Ajustado",
                    data=gerar_sped_ajustado,
                    file_name=f"SPED_AJUSTADO_{nome_empresa}.txt",
                    mime="text/plain",
                    use_container_width=True,
                    help="O arquivo é montado no momento do clique."
                )
                
                if os.path.exists("Conjunto SPED.xml"):
                    st.markdown("---")
                    st.download_button(
                        "⬇️ Baixar Conjunto SPED (XML)",
                        data=lambda: open("Conjunto SPED.xml", "rb").read(),
                        file_name="Conjunto SPED.xml",
                        mime="application/xml",
                        use_container_width=True
                    )

        # 2. BALANÇO (I155) - OPÇÃO INICIAL/FINAL
        with col2:
            st.markdown("**2. Balanço (I155)**")
            
            tipo_saldo = st.radio("Referência do Saldo:", ["Inicial (Abertura)", "Final (Fechamento)"])
            
            data_padrao = datetime.today()
            if tipo_saldo == "Inicial (Abertura)" and dt_inicial_sped: 
                data_padrao = dt_inicial_sped - timedelta(days=1)
            elif tipo_saldo == "Final (Fechamento)" and dt_final_sped:
                data_padrao = dt_final_sped
                
            data_balanco = st.date_input("Data p/ Balanço:", data_padrao, format="DD/MM/YYYY")
            dt_fmt = data_balanco.strftime("%d/%m/%Y")
            
            if st.button("🔍 Processar Balanço"):
                # Guarda apenas os TOTAIS em session_state. Antes guardava
                # também os bytes do arquivo, que ficavam retidos na sessão.
                _, total_debito, total_credito, has_balanco = montar_balanco(tipo_saldo, dt_fmt)
                st.session_state.balanco_totais = {"D": total_debito, "C": total_credito}
                st.session_state.balanco_processado = True
                st.session_state.balanco_has_data = has_balanco
                st.rerun()

            if st.session_state.balanco_processado:
                tot = st.session_state.balanco_totais
                diff = tot["D"] - tot["C"]
                st.markdown("---")
                st.caption(f"Débitos: {format_moeda(tot['D'])}")
                st.caption(f"Créditos: {format_moeda(tot['C'])}")
                if abs(diff) > 0.01: st.error(f"Diferença: {format_moeda(diff)}")
                else: st.success("Diferença: R$ 0,00")

                if st.session_state.balanco_has_data and pendentes == 0:
                    st.download_button(
                        "💾 Baixar Balanço",
                        data=lambda: "\r\n".join(
                            montar_balanco(tipo_saldo, dt_fmt)[0]
                        ).encode("latin-1", errors="replace"),
                        file_name=f"BALANCO_{nome_empresa}_{dt_fmt.replace('/','')}.txt",
                        mime="text/plain",
                        use_container_width=True
                    )
                elif pendentes > 0: st.warning("Resolva pendências.")
                else: st.warning("Sem dados.")

        # 3. CONFERÊNCIA E CONFIGURAÇÃO
        with col3:
            st.markdown("**3. Conferência**")
            lista_pendentes = [c for c in contas_origem if c['cod'] not in map_final_para_geracao]
            if lista_pendentes:
                st.warning(f"{len(lista_pendentes)} pendentes.")
                st.download_button(
                    "📑 Relatório CSV",
                    data=lambda: pd.DataFrame(lista_pendentes).to_csv(index=False, sep=';').encode("utf-8-sig"),
                    file_name="contas_pendentes.csv",
                    mime="text/csv",
                    use_container_width=True
                )
            else: 
                st.success("✅ Tudo Mapeado OK!")

    else: st.error("Nenhuma conta com movimento detectada.")

if 'de_para_map' in st.session_state and len(st.session_state.de_para_map) > 0:
    with placeholder_botao_salvar:
        backup_data = {
            "de_para_map": st.session_state.de_para_map,
            "conferidos": st.session_state.conferidos
        }
        st.download_button("⬇️ Salvar Backup Total", json.dumps(backup_data, indent=4), "backup_mapeamento_ecd.json", "application/json", help="Salva todo o progresso (incluindo checagens) desta empresa específica.")

    with placeholder_botao_modelo:
        modelo_data = {
            "de_para_map": st.session_state.de_para_map
        }
        st.download_button("⬇️ Exportar Apenas Modelo (DE/PARA)", json.dumps(modelo_data, indent=4), "modelo_de_para_compartilhado.json", "application/json", help="Exporta apenas o dicionário de relacionamentos para aplicar em outras empresas.")
else: st.info("Aguardando arquivos...")

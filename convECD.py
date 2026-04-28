import streamlit as st
import pandas as pd
from thefuzz import process, fuzz
import os
import io
import json
import re
from datetime import datetime, timedelta

st.set_page_config(page_title="DE/PARA SPED ECD", layout="wide")

st.markdown("<style>.cont-row {border-bottom: 1px solid #f0f2f6; padding: 15px 0px;}</style>", unsafe_allow_html=True)

st.title("🛠️ Conversor de Lançamentos ECD")
st.info("Foco: Substituição pelo **Código Reduzido** com indicadores de progresso.")

# --- INICIALIZAÇÃO DO ESTADO ---
if 'de_para_map' not in st.session_state:
    st.session_state.de_para_map = {}

if 'balanco_processado' not in st.session_state:
    st.session_state.balanco_processado = False
    st.session_state.balanco_dados = None
    st.session_state.balanco_totais = {}

if 'i157_processado' not in st.session_state:
    st.session_state.i157_processado = False
    st.session_state.i157_dados = None
    st.session_state.i157_has_data = False

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
    elif valor == "-- SELECIONE --":
        if str(cod_conta) in st.session_state.de_para_map:
            del st.session_state.de_para_map[str(cod_conta)]

def format_moeda(valor):
    return f"R$ {valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

def ler_arquivo_texto_seguro(file):
    raw_data = file.getvalue()
    try:
        content = raw_data.decode("latin-1")
    except UnicodeError:
        content = raw_data.decode("cp1252", errors="ignore")
    return [line.strip('\r\n') for line in content.splitlines() if line.strip()]

# --- SIDEBAR ---
st.sidebar.header("Configurações")
file_sped = st.sidebar.file_uploader("1. Arquivo SPED (TXT)", type=["txt"])
usar_padrao = st.sidebar.checkbox("Usar Plano de Contas Padrão UNSÃO?", value=True)

df_novo = None
if usar_padrao:
    caminho_padrao = "plano_padrao.xlsx"
    if os.path.exists(caminho_padrao):
        try:
            df_novo = pd.read_excel(caminho_padrao, header=None).iloc[:, [0, 1, 2]]
            df_novo.columns = ['Código', 'Classificação', 'Nome']
        except: st.sidebar.error("Erro ao ler plano_padrao.xlsx")
else:
    file_excel = st.sidebar.file_uploader("2. Subir Novo Plano (Excel)", type=["xlsx"])
    if file_excel:
        df_novo = pd.read_excel(file_excel, header=None).iloc[:, [0, 1, 2]]
        df_novo.columns = ['Código', 'Classificação', 'Nome']

# --- SEÇÃO BACKUP ---
st.sidebar.divider()
arquivo_backup = st.sidebar.file_uploader("Carregar Progresso Salvo (.json)", type=["json"], key="backup_upload")
if arquivo_backup is not None:
    dados = json.load(arquivo_backup)
    st.session_state.de_para_map.update({str(k): str(v) for k, v in dados.items()})

placeholder_botao_salvar = st.sidebar.empty()
ocultar_mapeadas = st.sidebar.checkbox("Ocultar contas já mapeadas?", value=False)

# --- Lógica Principal ---
if file_sped and df_novo is not None:
    df_novo = df_novo.astype(str)
    df_novo['Display'] = df_novo['Código'] + " | " + df_novo['Classificação'] + " - " + df_novo['Nome']
    df_novo['Grupo'] = df_novo['Classificação'].str.lstrip('0').str[0]

    content_sped = ler_arquivo_texto_seguro(file_sped)
    
    nome_empresa = "EMPRESA"
    dt_inicial_sped = None
    
    for line in content_sped:
        if line.startswith("|0000|"):
            parts = line.split("|")
            if len(parts) > 5: nome_empresa = limpar_nome_arquivo(parts[5])
            if len(parts) > 3: dt_inicial_sped = datetime.strptime(parts[3], "%d%m%Y").date()
            break
    
    # --- PASSO 1: IDENTIFICAR TODAS AS CONTAS ANALÍTICAS COM SALDO/MOVIMENTO ---
    initial_balances = {}
    contas_ativas = set()
    rtl_count_i150 = 0
    
    for line in content_sped:
        if line.startswith("|I150|"):
            rtl_count_i150 += 1
        elif line.startswith("|I155|"):
            reg = line.split("|")
            if len(reg) > 5:
                cod = reg[2].strip()
                contas_ativas.add(cod)
                if rtl_count_i150 == 1:
                    initial_balances[cod] = (reg[4].strip(), reg[5].strip())
        elif line.startswith("|I250|"):
            reg = line.split("|")
            if len(reg) > 2: contas_ativas.add(reg[2].strip())
        elif line.startswith("|J100|"):
            reg = line.split("|")
            if len(reg) > 8:
                cod_j = reg[2].strip()
                if cod_j not in initial_balances and reg[8].strip() != "0,00":
                    initial_balances[cod_j] = (reg[8].strip(), reg[9].strip())
                    contas_ativas.add(cod_j)

    # --- PASSO 2: FILTRAR O PLANO DE CONTAS (I050) SOMENTE PELAS ANALÍTICAS ATIVAS ---
    contas_origem_data = []
    info_contas_base = {} 
    
    for line in content_sped:
        if line.startswith("|I050|"):
            reg = line.split("|")
            if len(reg) > 6:
                tipo_conta = reg[4].strip() # S ou A
                cod_lido = reg[6].strip()
                
                # SÓ LEVA PARA A TELA SE FOR ANALÍTICA E ESTIVER NOS SALDOS/MOVIMENTOS
                if tipo_conta == 'A' and cod_lido in contas_ativas:
                    nome_conta = "Sem Nome"
                    for j in range(7, len(reg)):
                        if len(reg[j].strip()) > 2 and not reg[j].replace(".","").isnumeric():
                            nome_conta = reg[j].strip(); break
                    
                    classif_limpa = reg[5].strip().lstrip('0')
                    contas_origem_data.append({
                        "cod": cod_lido, 
                        "classif": reg[5].strip(), 
                        "nome": nome_conta, 
                        "grupo": classif_limpa[0] if classif_limpa else ""
                    })
                    info_contas_base[cod_lido] = True

    # CAPTURA DE CONTAS ÓRFÃS
    for cod_orfao in contas_ativas:
        if cod_orfao not in info_contas_base:
            contas_origem_data.append({
                "cod": cod_orfao, "classif": cod_orfao,
                "nome": f"⚠️ CONTA NÃO DECLARADA NO I050 ({cod_orfao})",
                "grupo": cod_orfao.lstrip('0')[0] if cod_orfao.lstrip('0') else ""
            })
    
    df_origem = pd.DataFrame(contas_origem_data).drop_duplicates(subset=['cod'])

    if not df_origem.empty:
        total_mapeadas_count = 0
        map_final_para_geracao = st.session_state.de_para_map.copy()
        process_data = []

        for idx, row in df_origem.iterrows():
            cod_atual = str(row['cod'])
            grupo_atual = row['grupo']
            df_filtrado = df_novo[df_novo['Grupo'] == grupo_atual]
            df_busca = df_filtrado if not df_filtrado.empty else df_novo
            
            if grupo_atual in ['1', '2']: df_opcoes = df_filtrado if not df_filtrado.empty else df_novo
            else:
                df_opcoes = df_novo[~df_novo['Grupo'].isin(['1', '2'])]
                if df_opcoes.empty: df_opcoes = df_novo 
            
            lista_nomes = df_busca['Nome'].tolist()
            candidatos = process.extract(row['nome'], lista_nomes, scorer=fuzz.token_set_ratio, limit=5)
            melhor_match, melhor_score_final = None, -1
            for nome_cand, score_flexivel in candidatos:
                score_rigido = fuzz.token_sort_ratio(row['nome'], nome_cand)
                media = (score_flexivel + score_rigido) / 2
                if media > melhor_score_final: melhor_score_final, melhor_match = media, nome_cand
            
            score = int(melhor_score_final)
            cod_sugerido_ia, display_sugerido_ia = None, None
            if score >= 65:
                match_row = df_busca[df_busca['Nome'] == melhor_match]
                if not match_row.empty:
                    cod_sugerido_ia = match_row.iloc[0]['Código']
                    display_sugerido_ia = match_row.iloc[0]['Display']
            
            esta_no_mapa = cod_atual in st.session_state.de_para_map
            if esta_no_mapa: total_mapeadas_count += 1
            elif score >= 65:
                total_mapeadas_count += 1
                map_final_para_geracao[cod_atual] = cod_sugerido_ia

            process_data.append({
                "row": row, "df_opcoes": df_opcoes, "score": score,
                "display_sugerido_ia": display_sugerido_ia,
                "esta_no_mapa": esta_no_mapa,
                "valor_no_mapa": str(st.session_state.de_para_map.get(cod_atual, ""))
            })

        st.subheader("🔗 Mapeamento de Contas")
        for item in process_data:
            row, cod_atual = item['row'], str(item['row']['cod'])
            if ocultar_mapeadas and item['esta_no_mapa']: continue

            with st.container():
                col_origem, col_destino = st.columns([1, 1])
                with col_origem:
                    st.markdown(f"**{row['nome']}**")
                    st.caption(f"Cod no SPED: {cod_atual} | Grupo: {row['grupo']}")
                
                with col_destino:
                    opcoes = ["-- SELECIONE --", "📝 -- DIGITAR MANUALMENTE --"] + item['df_opcoes']['Display'].tolist()
                    chave_select = f"sel_{cod_atual}"
                    
                    if item['esta_no_mapa']:
                        match_row = df_novo[df_novo['Código'] == item['valor_no_mapa']]
                        valor_inicial = match_row.iloc[0]['Display'] if not match_row.empty else "📝 -- DIGITAR MANUALMENTE --"
                    elif item['display_sugerido_ia']: valor_inicial = item['display_sugerido_ia']
                    else: valor_inicial = "-- SELECIONE --"

                    if valor_inicial not in opcoes: opcoes.insert(2, valor_inicial)
                    
                    escolha = st.selectbox(label=f"sel_{cod_atual}", options=opcoes, index=opcoes.index(valor_inicial), key=chave_select, label_visibility="collapsed")
                    if escolha == "📝 -- DIGITAR MANUALMENTE --":
                        st.text_input(f"Manual {cod_atual}", value=st.session_state.de_para_map.get(cod_atual, ""), key=f"in_{cod_atual}", on_change=atualizar_manual, args=(cod_atual,))
                    elif escolha != "-- SELECIONE --":
                        st.session_state.de_para_map[cod_atual] = escolha.split(" | ")[0]
                st.markdown("---")

        st.divider()
        c1, c2, c3, c4 = st.columns(4)
        
        # 1. SPED AJUSTADO
        if len(df_origem) - total_mapeadas_count == 0:
            saida = []
            for line in content_sped:
                if any(line.startswith(p) for p in ["|I250|", "|I155|", "|I052|"]):
                    reg = line.split("|")
                    if len(reg) > 2 and reg[2] in map_final_para_geracao: reg[2] = str(map_final_para_geracao[reg[2]])
                    saida.append("|".join(reg))
                else: saida.append(line)
            sped_buffer = "\r\n".join(saida).encode("latin-1", errors="replace")
            c1.download_button("💾 Baixar SPED Ajustado", sped_buffer, f"SPED_{nome_empresa}.txt")

        # 2. BALANÇO (I155)
        with c2:
            if st.button("🔍 Gerar Balanço"):
                bal_lines = ["|6000|V||||"]
                dt_fmt = (dt_inicial_sped - timedelta(days=1)).strftime("%d/%m/%Y")
                for c_ant, n_cod in map_final_para_geracao.items():
                    v_str, dc = initial_balances.get(c_ant, ("0,00", "D"))
                    if v_str != "0,00":
                        lin = f"|6100|{dt_fmt}|{n_cod}||{v_str}||SALDO ABERTURA|||||" if dc=='D' else f"|6100|{dt_fmt}||{n_cod}|{v_str}||SALDO ABERTURA|||||"
                        bal_lines.append(lin)
                st.download_button("💾 Baixar Balanço", "\r\n".join(bal_lines).encode("latin-1", errors="replace"), "BALANCO.txt")

        # 3. I157
        with c3:
            if st.button("🔄 Gerar I157"):
                i157 = ["ID;;;;;;"]
                for c_ant, n_cod in map_final_para_geracao.items():
                    v_str, dc = initial_balances.get(c_ant, ("0,00", "D"))
                    if v_str != "0,00":
                        i157.append(f"C;{n_cod};{c_ant if c_ant.isnumeric() else ''};{c_ant if not c_ant.isnumeric() else ''};{v_str};{dc};")
                st.download_button("💾 Baixar I157", "\r\n".join(i157).encode("latin-1", errors="replace"), "I157.txt")

else: st.info("Aguardando arquivo SPED...")
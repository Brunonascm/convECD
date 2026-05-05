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

# --- CARREGAMENTO DO PLANO ---
df_novo = None
if usar_padrao:
    caminho_padrao = "plano_padrao.xlsx"
    if os.path.exists(caminho_padrao):
        try:
            df_novo = pd.read_excel(caminho_padrao, header=None).iloc[:, [0, 1, 2]]
            df_novo.columns = ['Código', 'Classificação', 'Nome']
        except:
            st.sidebar.error("Erro ao ler plano_padrao.xlsx")
    else:
        st.sidebar.warning("Arquivo 'plano_padrao.xlsx' não encontrado.")
else:
    file_excel = st.sidebar.file_uploader("2. Subir Novo Plano (Excel)", type=["xlsx"])
    
    with st.sidebar.expander("ℹ️ Ver Modelo / Baixar Exemplo"):
        st.write("Seu Excel deve seguir estritamente esta ordem (sem cabeçalho):")
        df_exemplo_visual = pd.DataFrame({
            "Coluna A": ["50", "51", "..."],
            "Coluna B": ["1.01.01", "1.01.02", "..."],
            "Coluna C": ["CAIXA GERAL", "BANCO CONTA MOV.", "..."]
        })
        st.table(df_exemplo_visual)
        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
            pd.DataFrame(columns=['A', 'B', 'C']).to_excel(writer, sheet_name='Plan1', header=False, index=False)
        st.download_button("⬇️ Baixar Planilha Modelo", buffer, "Modelo_Plano_Contas.xlsx", "application/vnd.ms-excel")

    if file_excel:
        df_novo = pd.read_excel(file_excel, header=None).iloc[:, [0, 1, 2]]
        df_novo.columns = ['Código', 'Classificação', 'Nome']

# --- SEÇÃO BACKUP ---
st.sidebar.divider()
st.sidebar.header("💾 Backup do Trabalho")

arquivo_backup = st.sidebar.file_uploader("Carregar Progresso Salvo (.json)", type=["json"], key="backup_upload")
if arquivo_backup is not None:
    try:
        file_id = f"{arquivo_backup.name}_{arquivo_backup.size}"
        if st.session_state.get("backup_id") != file_id:
            dados = json.load(arquivo_backup)
            dados_limpos = {str(k): str(v) for k, v in dados.items()}
            st.session_state.de_para_map.update(dados_limpos)
            
            for cod, val in dados_limpos.items():
                st.session_state[f"in_{cod}"] = val
            
            st.session_state["backup_id"] = file_id
            st.sidebar.success(f"Backup carregado! {len(dados_limpos)} contas.")
            st.rerun()
    except Exception as e:
        st.sidebar.error(f"Erro no backup: {e}")

placeholder_botao_salvar = st.sidebar.empty()

# --- FILTROS DE TELA ---
st.sidebar.divider()
st.sidebar.header("Filtros de Tela")
ocultar_mapeadas = st.sidebar.checkbox("Ocultar contas já mapeadas?", value=False)

# --- Lógica Principal ---
if file_sped and df_novo is not None:
    df_novo = df_novo.astype(str)
    df_novo['Display'] = df_novo['Código'] + " | " + df_novo['Classificação'] + " - " + df_novo['Nome']
    # AJUSTE GRUPO 0: lstrip
    df_novo['Grupo'] = df_novo['Classificação'].str.lstrip('0').str[0]

    content_sped = ler_arquivo_texto_seguro(file_sped)
    
    # --- EXTRAÇÃO DE DADOS DO CABEÇALHO (REGISTRO 0000) ---
    nome_empresa = "EMPRESA"
    dt_inicial_sped = None
    dt_final_sped = None
    
    for line in content_sped:
        if line.startswith("|0000|"):
            parts = line.split("|")
            if len(parts) > 5:
                nome_empresa = limpar_nome_arquivo(parts[5])
            if len(parts) > 3:
                try:
                    dt_str = parts[3] 
                    dt_inicial_sped = datetime.strptime(dt_str, "%d%m%Y").date()
                except: pass
            if len(parts) > 4:
                try:
                    dt_str_fin = parts[4] 
                    dt_final_sped = datetime.strptime(dt_str_fin, "%d%m%Y").date()
                except: pass
            break
    
    # --- EXTRAÇÃO INTELIGENTE DOS SALDOS E CAPTURA J100 ---
    initial_balances = {}
    final_balances = {}
    contas_com_movimento = set()
    map_cod_para_classif = {} # NOVO: Armazena a classificação para o I157
    rtl_count_i150 = 0
    
    for line in content_sped:
        if line.startswith("|I150|"):
            rtl_count_i150 += 1
        elif line.startswith("|I155|"):
            reg = line.split("|")
            if len(reg) >= 10:
                cod = reg[2].strip()
                contas_com_movimento.add(cod)
                val_ini_str = reg[4].strip()
                dc_ini = reg[5].strip()
                val_fin_str = reg[8].strip()
                dc_fin = reg[9].strip()
                if cod not in initial_balances:
                    initial_balances[cod] = (val_ini_str, dc_ini) if rtl_count_i150 <= 1 else ("0,00", dc_ini)
                final_balances[cod] = (val_fin_str, dc_fin)
        elif line.startswith("|I250|"):
            reg = line.split("|")
            if len(reg) > 2: contas_com_movimento.add(reg[2].strip())
        # RESGATE J100 (Capital Social Parado)
        elif line.startswith("|J100|"):
            reg = line.split("|")
            if len(reg) > 8:
                cod_j = reg[2].strip()
                if cod_j not in initial_balances and reg[8].strip() != "0,00":
                    initial_balances[cod_j] = (reg[8].strip(), reg[9].strip())
                    contas_com_movimento.add(cod_j)

    contas_origem_data = []
    info_contas_base = {} 
    for line in content_sped:
        if line.startswith("|I050|"):
            reg = line.split("|")
            if len(reg) > 6:
                classif_original = reg[5].strip()
                cod_encontrado = reg[6].strip()
                if cod_encontrado in contas_com_movimento:
                    map_cod_para_classif[cod_encontrado] = classif_original # Salva para o I157
                    nome_conta = "Sem Nome"
                    for j in range(7, len(reg)):
                        if len(reg[j].strip()) > 2 and not reg[j].replace(".","").isnumeric():
                            nome_conta = reg[j].strip(); break
                    contas_origem_data.append({
                        "cod": cod_encontrado, 
                        "classif": classif_original, 
                        "nome": nome_conta, 
                        "grupo": classif_original.lstrip('0')[0] if classif_original.lstrip('0') else ""
                    })
                    info_contas_base[cod_encontrado] = True

    # CAPTURA ÓRFÃS
    for cod_orfao in contas_com_movimento:
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
                    cod_sugerido_ia, display_sugerido_ia = match_row.iloc[0]['Código'], match_row.iloc[0]['Display']
            
            esta_no_mapa = cod_atual in st.session_state.de_para_map
            valor_no_mapa = str(st.session_state.de_para_map.get(cod_atual, ""))
            resolvida, is_manual = False, False
            
            if esta_no_mapa:
                resolvida = True
                if valor_no_mapa != cod_sugerido_ia: is_manual = True
            elif score >= 65:
                resolvida = True
                map_final_para_geracao[cod_atual] = cod_sugerido_ia

            if resolvida: total_mapeadas_count += 1
            
            process_data.append({
                "row": row, "df_opcoes": df_opcoes, "score": score,
                "cod_sugerido_ia": cod_sugerido_ia, "display_sugerido_ia": display_sugerido_ia,
                "resolvida": resolvida, "is_manual": is_manual, "esta_no_mapa": esta_no_mapa, "valor_no_mapa": valor_no_mapa
            })

        st.subheader("🔗 Mapeamento de Contas")
        for item in process_data:
            row, cod_atual = item['row'], str(item['row']['cod'])
            if ocultar_mapeadas and item['resolvida']: continue

            with st.container():
                col_origem, col_destino = st.columns([1, 1])
                with col_origem:
                    st.markdown(f"**{row['nome']}**")
                    st.caption(f"Cod no SPED: {cod_atual} | Grupo: {row['grupo']}")
                
                with col_destino:
                    df_opcoes = item['df_opcoes']
                    opcoes = ["-- SELECIONE --", "📝 -- DIGITAR MANUALMENTE --"] + df_opcoes['Display'].tolist()
                    chave_select, valor_inicial = f"sel_{cod_atual}", opcoes[0]
                    
                    if item['esta_no_mapa']:
                        match_row = df_novo[df_novo['Código'] == item['valor_no_mapa']]
                        valor_inicial = match_row.iloc[0]['Display'] if not match_row.empty else "📝 -- DIGITAR MANUALMENTE --"
                    elif item['display_sugerido_ia']: valor_inicial = item['display_sugerido_ia']

                    if valor_inicial not in opcoes: opcoes.insert(2, valor_inicial)
                    
                    escolha = st.selectbox(label=f"sel_{cod_atual}", options=opcoes, index=opcoes.index(valor_inicial), key=chave_select, label_visibility="collapsed")
                    if escolha == "📝 -- DIGITAR MANUALMENTE --":
                        st.text_input(f"Cód. manual para {cod_atual}:", value=st.session_state.de_para_map.get(cod_atual, ""), key=f"in_{cod_atual}", on_change=atualizar_manual, args=(cod_atual,))
                    elif escolha != "-- SELECIONE --":
                        st.session_state.de_para_map[cod_atual] = escolha.split(" | ")[0]
                st.markdown("---")

        st.divider()
        col_m1, col_m2, col_m3 = st.columns(3)
        perc_concluido = (total_mapeadas_count / len(df_origem)) * 100 if len(df_origem) > 0 else 0
        col_m1.metric("Total", len(df_origem)); col_m2.metric("Mapeadas", total_mapeadas_count, f"{perc_concluido:.1f}%"); col_m3.metric("Pendentes", len(df_origem) - total_mapeadas_count)

        st.divider()
        st.subheader("📂 Finalização, Relatórios e Downloads")
        col1, col2, col3, col4 = st.columns(4)

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
            col1.download_button("💾 Baixar SPED Ajustado", sped_buffer, f"SPED_AJUSTADO_{nome_empresa}.txt", use_container_width=True)

        # 2. BALANÇO (I155)
        with col2:
            data_padrao = (dt_inicial_sped - timedelta(days=1)) if dt_inicial_sped else datetime.today()
            data_balanco = st.date_input("Data p/ Balanço:", data_padrao, format="DD/MM/YYYY")
            dt_fmt = data_balanco.strftime("%d/%m/%Y")
            if st.button("🔍 Processar Balanço"):
                bal_lines = ["|6000|V||||"]
                for cod, novo in map_final_para_geracao.items():
                    v_str, dc = initial_balances.get(cod, ("0,00", "D"))
                    if v_str != "0,00":
                        linha = f"|6100|{dt_fmt}|{novo}||{v_str}||SALDO DE ABERTURA|||||" if dc == 'D' else f"|6100|{dt_fmt}||{novo}|{v_str}||SALDO DE ABERTURA|||||"
                        bal_lines.append(linha)
                st.session_state.balanco_dados = "\r\n".join(bal_lines).encode("latin-1", errors="replace")
                st.session_state.balanco_processado = True; st.rerun()
            if st.session_state.balanco_processado:
                st.download_button("💾 Baixar Balanço", st.session_state.balanco_dados, "BALANCO.txt", use_container_width=True)

        # 3. I157 (LEIAUTE DOMÍNIO CORRIGIDO)
        with col3:
            if st.button("🔄 Processar I157"):
                i157_lines = ["ID;;;;;;"]
                for cod_antigo, cod_novo in map_final_para_geracao.items():
                    v_str, dc = initial_balances.get(cod_antigo, ("0,00", "D"))
                    classif_ant = map_cod_para_classif.get(cod_antigo, "") # Captura classificação real
                    if v_str != "0,00":
                        # C;Novo;ReduzidoAntigo;ClassificaçãoAntiga;Valor;D/C;
                        i157_lines.append(f"C;{cod_novo};{cod_antigo if cod_antigo.isnumeric() else ''};{classif_ant};{v_str};{dc};")
                st.session_state.i157_dados = "\r\n".join(i157_lines).encode("latin-1", errors="replace")
                st.session_state.i157_processado = True; st.rerun()
            if st.session_state.i157_processado:
                st.download_button("💾 Baixar I157", st.session_state.i157_dados, "I157.txt", use_container_width=True)

else: st.info("Aguardando arquivos...")
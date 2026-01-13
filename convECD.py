import streamlit as st
import pandas as pd
from thefuzz import process, fuzz
import os
import io
import json
from datetime import datetime

st.set_page_config(page_title="DE/PARA SPED ECD", layout="wide")

st.markdown("<style>.cont-row {border-bottom: 1px solid #f0f2f6; padding: 15px 0px;}</style>", unsafe_allow_html=True)

st.title("🛠️ Conversor de Lançamentos ECD")
st.info("Foco: Substituição pelo **Código Reduzido** com indicadores de progresso.")

# --- INICIALIZAÇÃO DO ESTADO ---
if 'de_para_map' not in st.session_state:
    st.session_state.de_para_map = {}

# --- CALLBACKS ---
def atualizar_manual(cod_conta):
    chave_input = f"in_{cod_conta}"
    if chave_input in st.session_state:
        valor = st.session_state[chave_input]
        if valor:
            st.session_state.de_para_map[str(cod_conta)] = str(valor)

# --- SIDEBAR ---
st.sidebar.header("Configurações")
file_sped = st.sidebar.file_uploader("1. Arquivo SPED (TXT)", type=["txt"])
usar_padrao = st.sidebar.checkbox("Usar Plano de Contas Padrão?", value=True)

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

# Placeholder para o botão de salvar aparecer aqui visualmente
placeholder_botao_salvar = st.sidebar.empty()

# --- NOVO SISTEMA DE FILTROS (CHECKBOX) ---
st.sidebar.divider()
st.sidebar.header("Filtros de Tela")
# O padrão é False (Não ocultar), assim o usuário vê o que acabou de fazer
ocultar_mapeadas = st.sidebar.checkbox("Ocultar contas já mapeadas?", value=False)

def ler_arquivo_texto(file):
    raw_data = file.getvalue()
    content = raw_data
    for encoding in ["cp1252", "utf-8", "latin-1"]:
        try:
            content = raw_data.decode(encoding)
            break
        except UnicodeError: continue
    if isinstance(content, bytes): content = content.decode("latin-1")
    return [linha.strip() for linha in content.splitlines() if linha.strip()]

# --- Lógica Principal ---
if file_sped and df_novo is not None:
    df_novo = df_novo.astype(str)
    df_novo['Display'] = df_novo['Código'] + " | " + df_novo['Classificação'] + " - " + df_novo['Nome']
    df_novo['Grupo'] = df_novo['Classificação'].str[0]

    content_sped = ler_arquivo_texto(file_sped)
    
    contas_com_movimento = set()
    for line in content_sped:
        if "|I250|" in line:
            reg = line.split("|")
            if len(reg) > 2: contas_com_movimento.add(reg[2].strip())

    contas_origem_data = []
    for line in content_sped:
        if "|I050|" in line:
            reg = line.split("|")
            if len(reg) > 6:
                cod_encontrado = None
                pos_classif = -1
                for i in [5, 6, 7]:
                    if i < len(reg) and reg[i].strip() in contas_com_movimento:
                        cod_encontrado = reg[i].strip()
                        pos_classif = i
                        break
                if cod_encontrado:
                    nome_conta = "Sem Nome"
                    for j in range(pos_classif + 1, len(reg)):
                        if len(reg[j]) > 3 and not reg[j].replace(".","").isnumeric():
                            nome_conta = reg[j].strip()
                            break
                    contas_origem_data.append({
                        "cod": cod_encontrado, 
                        "classif": reg[pos_classif].strip(), 
                        "nome": nome_conta, 
                        "grupo": reg[pos_classif][0] if len(reg[pos_classif]) > 0 else ""
                    })
    
    df_origem = pd.DataFrame(contas_origem_data).drop_duplicates()

    if not df_origem.empty:
        
        # --- CÁLCULOS ---
        total_mapeadas_count = 0
        map_final_para_geracao = st.session_state.de_para_map.copy()
        process_data = []

        for idx, row in df_origem.iterrows():
            cod_atual = str(row['cod'])
            grupo_atual = row['grupo']
            
            df_filtrado = df_novo[df_novo['Grupo'] == grupo_atual]
            df_busca = df_filtrado if not df_filtrado.empty else df_novo
            lista_nomes = df_busca['Nome'].tolist()
            
            candidatos = process.extract(row['nome'], lista_nomes, scorer=fuzz.token_set_ratio, limit=5)
            melhor_match = None
            melhor_score_final = -1
            for nome_cand, score_flexivel in candidatos:
                score_rigido = fuzz.token_sort_ratio(row['nome'], nome_cand)
                media = (score_flexivel + score_rigido) / 2
                if media > melhor_score_final:
                    melhor_score_final = media
                    melhor_match = nome_cand
            
            score = int(melhor_score_final)
            
            cod_sugerido_ia = None
            display_sugerido_ia = None
            if score >= 65:
                match_row = df_busca[df_busca['Nome'] == melhor_match]
                if not match_row.empty:
                    cod_sugerido_ia = match_row.iloc[0]['Código']
                    display_sugerido_ia = match_row.iloc[0]['Display']
            
            esta_no_mapa = cod_atual in st.session_state.de_para_map
            valor_no_mapa = str(st.session_state.de_para_map.get(cod_atual, ""))
            
            resolvida = False
            is_manual = False
            
            if esta_no_mapa:
                resolvida = True
                if valor_no_mapa != cod_sugerido_ia:
                    is_manual = True
            elif score >= 65:
                resolvida = True
                map_final_para_geracao[cod_atual] = cod_sugerido_ia

            if resolvida:
                total_mapeadas_count += 1
            
            process_data.append({
                "row": row,
                "df_busca": df_busca,
                "score": score,
                "cod_sugerido_ia": cod_sugerido_ia,
                "display_sugerido_ia": display_sugerido_ia,
                "resolvida": resolvida,
                "is_manual": is_manual,
                "esta_no_mapa": esta_no_mapa,
                "valor_no_mapa": valor_no_mapa
            })

        # --- EXIBIÇÃO ---
        st.subheader("🔗 Mapeamento de Contas")
        
        for item in process_data:
            row = item['row']
            cod_atual = str(row['cod'])
            resolvida = item['resolvida']
            esta_no_mapa = item['esta_no_mapa']
            
            # --- FILTRO SIMPLIFICADO ---
            # Se o usuário marcou o checkbox "Ocultar", pulamos as resolvidas.
            if ocultar_mapeadas and resolvida:
                continue

            with st.container():
                col_origem, col_destino = st.columns([1, 1])
                
                with col_origem:
                    st.markdown(f"**{row['nome']}**")
                    st.caption(f"Cod no SPED: {cod_atual} | Grupo: {row['grupo']}")
                
                with col_destino:
                    df_busca = item['df_busca']
                    opcoes = ["-- SELECIONE --", "📝 -- DIGITAR MANUALMENTE --"] + df_busca['Display'].tolist()
                    
                    chave_select = f"sel_{cod_atual}"
                    valor_inicial = opcoes[0]
                    
                    if esta_no_mapa:
                        match_row = df_busca[df_busca['Código'] == item['valor_no_mapa']]
                        if not match_row.empty:
                            valor_inicial = match_row.iloc[0]['Display']
                        else:
                            valor_inicial = "📝 -- DIGITAR MANUALMENTE --"
                            if f"in_{cod_atual}" not in st.session_state:
                                st.session_state[f"in_{cod_atual}"] = item['valor_no_mapa']
                    elif item['display_sugerido_ia']:
                        if chave_select not in st.session_state:
                            valor_inicial = item['display_sugerido_ia']
                        else:
                            valor_inicial = st.session_state[chave_select]

                    if chave_select not in st.session_state:
                        st.session_state[chave_select] = valor_inicial

                    # Status Visual
                    if item['is_manual']:
                        st.info("📌 Mapeado Manualmente")
                    elif item['score'] >= 65:
                        st.success(f"✅ Sugestão: {item['score']}%")
                    else:
                        st.warning(f"⚠️ Similaridade baixa ({item['score']}%)")

                    # Selectbox (Sem callback complexo para evitar conflito com filtro)
                    escolha = st.selectbox(
                        label=f"sel_{cod_atual}", 
                        options=opcoes, 
                        key=chave_select,
                        label_visibility="collapsed"
                    )

                    # Lógica de Salvamento Direta
                    novo_valor = None
                    
                    if escolha == "📝 -- DIGITAR MANUALMENTE --":
                        pass
                    elif escolha != "-- SELECIONE --":
                        try:
                            cod_reduzido = df_busca[df_busca['Display'] == escolha].iloc[0]['Código']
                            # Só salva se mudou
                            if str(cod_reduzido) != item['valor_no_mapa']:
                                novo_valor = str(cod_reduzido)
                        except: pass
                    elif escolha == "-- SELECIONE --" and esta_no_mapa:
                        del st.session_state.de_para_map[cod_atual]
                        st.rerun()

                    if novo_valor:
                        st.session_state.de_para_map[cod_atual] = novo_valor
                        st.rerun()

                    # Input Manual
                    if escolha == "📝 -- DIGITAR MANUALMENTE --":
                        valor_ant = st.session_state.de_para_map.get(cod_atual, "")
                        st.text_input(
                            f"Cód. manual para {cod_atual}:", 
                            value=valor_ant, 
                            key=f"in_{cod_atual}",
                            on_change=atualizar_manual,
                            args=(cod_atual,)
                        )
                st.markdown("---")

        st.divider()
        
        total = len(df_origem)
        mapeadas = total_mapeadas_count 
        pendentes = total - mapeadas
        perc_concluido = (mapeadas / total) * 100 if total > 0 else 0

        col_m1, col_m2, col_m3 = st.columns(3)
        col_m1.metric("Total", total)
        col_m2.metric("Mapeadas (Inclui Sugestões)", mapeadas, f"{perc_concluido:.1f}%")
        col_m3.metric("Pendentes", pendentes, f"-{pendentes}", delta_color="inverse")

        # --- FINALIZAÇÃO ---
        st.divider()
        st.subheader("📂 Finalização, Relatórios e Downloads")
        
        col1, col2, col3, col4 = st.columns(4)

        with col1:
            st.markdown("**1. Arquivo Final**")
            if pendentes > 0: st.warning(f"⚠️ Faltam {pendentes}.")
            
            if st.button("🚀 Gerar SPED", disabled=(pendentes > 0), use_container_width=True):
                saida = []
                for line in content_sped:
                    if "|I250|" in line:
                        reg = line.split("|")
                        if len(reg) > 2 and reg[2] in map_final_para_geracao:
                            reg[2] = map_final_para_geracao[reg[2]]
                        saida.append("|".join(reg))
                    else:
                        saida.append(line)
                st.download_button("💾 Baixar TXT", "\n".join(saida), "SPED_AJUSTADO.txt", use_container_width=True)

        with col2:
            st.markdown("**2. Balanço de abertura (I155)**")
            data_balanco = st.date_input("Data:", datetime.today(), format="DD/MM/YYYY")
            if st.button("📊 Gerar Balanço", disabled=(pendentes > 0), use_container_width=True):
                dt_fmt = data_balanco.strftime("%d/%m/%Y")
                saida_balanco = ["|6000|V||||"]
                rtl_count = 0
                linhas = 0
                for line in content_sped:
                    if "|I150|" in line: rtl_count += 1
                    if rtl_count == 1 and "|I155|" in line:
                        reg = line.split("|")
                        if len(reg) > 5:
                            cod = reg[2].strip()
                            if cod in map_final_para_geracao:
                                novo = map_final_para_geracao[cod]
                                hist = f"SALDO DE ABERTURA EM {dt_fmt}"
                                linha = f"|6100|{dt_fmt}|{novo}||{reg[4]}||{hist}||||||" if reg[5]=='D' else f"|6100|{dt_fmt}||{novo}|{reg[4]}||{hist}||||||"
                                saida_balanco.append(linha)
                                linhas += 1
                if linhas > 0: st.download_button("💾 Baixar Balanço", "\n".join(saida_balanco), f"BALANCO_{dt_fmt.replace('/','')}.txt", use_container_width=True)
                else: st.warning("Sem dados.")

        with col3:
            st.markdown("**3. Conferência**")
            df_pend = df_origem[~df_origem['cod'].isin(map_final_para_geracao.keys())]
            if not df_pend.empty:
                st.warning(f"{len(df_pend)} pendentes.")
                st.download_button("📑 Relatório CSV", df_pend.to_csv(index=False, sep=';', encoding='utf-8-sig'), "contas_pendentes.csv", "text/csv", use_container_width=True)
            else: st.success("✅ Tudo OK!")

        with col4:
            st.markdown("**4. Configuração**")
            if os.path.exists("Conjunto SPED.xml"):
                with open("Conjunto SPED.xml", "rb") as f:
                    st.download_button("⬇️ Conjunto de Dados", f.read(), "Conjunto SPED.xml", "application/xml", use_container_width=True)
            else: st.info("XML indisponível.")

    else:
        st.error("Nenhuma conta com movimento detectada.")

# --- RENDERIZA O BOTÃO NO ESPAÇO RESERVADO LÁ EM CIMA ---
if 'de_para_map' in st.session_state and len(st.session_state.de_para_map) > 0:
    with placeholder_botao_salvar:
        st.download_button(
            "⬇️ Salvar Progresso Atual", 
            json.dumps(st.session_state.de_para_map, indent=4), 
            "backup_mapeamento_ecd.json", 
            "application/json",
            help="Baixe o arquivo JSON para continuar depois."
        )
else:
    st.info("Aguardando arquivos...")
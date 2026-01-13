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

# 1. Carregar
arquivo_backup = st.sidebar.file_uploader("Carregar Progresso Salvo (.json)", type=["json"], key="backup_upload")
if arquivo_backup is not None:
    try:
        file_id = f"{arquivo_backup.name}_{arquivo_backup.size}"
        if st.session_state.get("backup_id") != file_id:
            dados = json.load(arquivo_backup)
            
            # Garante que tudo seja string para evitar erro de tipo
            dados_limpos = {str(k): str(v) for k, v in dados.items()}
            
            # Atualiza o dicionário de dados
            st.session_state.de_para_map.update(dados_limpos)
            
            # Marca que este arquivo já foi processado
            st.session_state["backup_id"] = file_id
            
            st.sidebar.success(f"Backup carregado! {len(dados_limpos)} contas recuperadas.")
            st.rerun() # Reinicia para aplicar visualmente
    except Exception as e:
        st.sidebar.error(f"Erro no backup: {e}")

# 2. Salvar
if len(st.session_state.de_para_map) > 0:
    st.sidebar.download_button(
        "⬇️ Salvar Progresso Atual", 
        json.dumps(st.session_state.de_para_map, indent=4), 
        "backup_mapeamento_ecd.json", 
        "application/json"
    )

# --- FILTROS DE TELA ---
st.sidebar.divider()
st.sidebar.header("Filtros de Tela")
filtro_status = st.sidebar.selectbox("Mostrar na lista:", ["Todas", "Apenas Pendentes", "Apenas Mapeadas"])

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
    # Formatação robusta do DataFrame de Destino
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
        st.subheader("🔗 Mapeamento de Contas")
        
        for idx, row in df_origem.iterrows():
            cod_atual = str(row['cod'])
            foi_mapeada = cod_atual in st.session_state.de_para_map

            if filtro_status == "Apenas Pendentes" and foi_mapeada: continue
            if filtro_status == "Apenas Mapeadas" and not foi_mapeada: continue

            with st.container():
                col_origem, col_destino = st.columns([1, 1])
                grupo_atual = row['grupo']
                df_filtrado = df_novo[df_novo['Grupo'] == grupo_atual]
                df_busca = df_filtrado if not df_filtrado.empty else df_novo
                
                with col_origem:
                    st.markdown(f"**{row['nome']}**")
                    st.caption(f"Cod no SPED: {cod_atual} | Grupo: {grupo_atual}")
                
                with col_destino:
                    lista_nomes = df_busca['Nome'].tolist()
                    
                    # Similaridade
                    candidatos = process.extract(row['nome'], lista_nomes, scorer=fuzz.token_set_ratio, limit=5)
                    melhor_match = None
                    melhor_score_final = -1
                    for nome_cand, score_flexivel in candidatos:
                        score_rigido = fuzz.token_sort_ratio(row['nome'], nome_cand)
                        media = (score_flexivel + score_rigido) / 2
                        if media > melhor_score_final:
                            melhor_score_final = media
                            melhor_match = nome_cand
                    
                    match_nome = melhor_match
                    score = int(melhor_score_final)
                    
                    opcoes = ["-- SELECIONE --", "📝 -- DIGITAR MANUALMENTE --"] + df_busca['Display'].tolist()
                    
                    # --- LÓGICA DE SINCRONIZAÇÃO VISUAL BLINDADA ---
                    idx_padrao = 0
                    manual_mode = False
                    status_msg = ""
                    status_type = "warning"
                    
                    if foi_mapeada:
                        # 1. Recupera o código salvo no backup/memória
                        valor_salvo = str(st.session_state.de_para_map[cod_atual])
                        
                        # 2. Tenta achar esse código na lista de opções visíveis
                        match_row = df_busca[df_busca['Código'] == valor_salvo]
                        
                        if not match_row.empty:
                            # Se achou na lista, pega o texto completo (Display)
                            display_text = match_row.iloc[0]['Display']
                            if display_text in opcoes:
                                idx_padrao = opcoes.index(display_text)
                                # AQUI ESTÁ A CORREÇÃO: Força o widget a assumir este valor
                                st.session_state[f"sel_{cod_atual}"] = display_text
                                status_msg = "📌 Recuperado do Backup"
                                status_type = "success"
                            else:
                                manual_mode = True
                        else:
                            # Se o código salvo não está na lista filtrada (ex: mudou de grupo), vai pro manual
                            manual_mode = True
                            st.session_state[f"in_{cod_atual}"] = valor_salvo
                    else:
                        # Se não tem backup, usa a sugestão da IA
                        if score >= 65:
                            sugestao_full = df_busca[df_busca['Nome'] == match_nome].iloc[0]['Display']
                            idx_padrao = opcoes.index(sugestao_full)
                            status_msg = f"✅ Sugestão: {score}%"
                            status_type = "success"
                        else:
                            status_msg = f"⚠️ Similaridade baixa ({score}%)"
                            status_type = "warning"

                    # Exibição do Status Visual
                    if manual_mode:
                        st.info(f"📝 Digitado Manualmente / Fora do Grupo")
                    elif status_type == "success":
                        st.success(status_msg)
                    else:
                        st.warning(status_msg)
                    
                    # Selectbox
                    escolha = st.selectbox(
                        label=f"sel_{cod_atual}", 
                        options=opcoes, 
                        index=idx_padrao, 
                        key=f"sel_{cod_atual}", # A chave conecta com o st.session_state forçado acima
                        label_visibility="collapsed"
                    )
                    
                    # Lógica de Salvamento pós-interação
                    if escolha == "📝 -- DIGITAR MANUALMENTE --":
                        valor_ant = st.session_state.de_para_map.get(cod_atual, "")
                        cod_manual = st.text_input(f"Cód. manual para {cod_atual}:", value=valor_ant, key=f"in_{cod_atual}")
                        if cod_manual: st.session_state.de_para_map[cod_atual] = str(cod_manual)
                    elif escolha != "-- SELECIONE --":
                        cod_reduzido = df_busca[df_busca['Display'] == escolha].iloc[0]['Código']
                        st.session_state.de_para_map[cod_atual] = str(cod_reduzido)
                    else:
                        if cod_atual in st.session_state.de_para_map: del st.session_state.de_para_map[cod_atual]
                st.markdown("---")

        st.divider()
        total = len(df_origem)
        mapeadas = len(st.session_state.de_para_map)
        pendentes = total - mapeadas
        perc_concluido = (mapeadas / total) * 100 if total > 0 else 0

        col_m1, col_m2, col_m3 = st.columns(3)
        col_m1.metric("Total", total)
        col_m2.metric("Mapeadas", mapeadas, f"{perc_concluido:.1f}%")
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
                        if len(reg) > 2 and reg[2] in st.session_state.de_para_map:
                            reg[2] = st.session_state.de_para_map[reg[2]]
                        saida.append("|".join(reg))
                    else:
                        saida.append(line)
                st.download_button("💾 Baixar TXT", "\n".join(saida), "SPED_AJUSTADO.txt", use_container_width=True)

        with col2:
            st.markdown("**2. Balanço de Abertura (I155)**")
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
                            if cod in st.session_state.de_para_map:
                                novo = st.session_state.de_para_map[cod]
                                hist = f"SALDO DE ABERTURA EM {dt_fmt}"
                                linha = f"|6100|{dt_fmt}|{novo}||{reg[4]}||{hist}||||||" if reg[5]=='D' else f"|6100|{dt_fmt}||{novo}|{reg[4]}||{hist}||||||"
                                saida_balanco.append(linha)
                                linhas += 1
                if linhas > 0: st.download_button("💾 Baixar Balanço", "\n".join(saida_balanco), f"BALANCO_{dt_fmt.replace('/','')}.txt", use_container_width=True)
                else: st.warning("Sem dados.")

        with col3:
            st.markdown("**3. Conferência**")
            df_pend = df_origem[~df_origem['cod'].isin(st.session_state.de_para_map.keys())]
            if not df_pend.empty:
                st.warning(f"{len(df_pend)} pendentes.")
                st.download_button("📑 Relatório CSV", df_pend.to_csv(index=False, sep=';', encoding='utf-8-sig'), "contas_pendentes.csv", "text/csv", use_container_width=True)
            else: st.success("✅ Tudo OK!")

        with col4:
            st.markdown("**4. Configuração**")
            if os.path.exists("Conjunto SPED.xml"):
                with open("Conjunto SPED.xml", "rb") as f:
                    st.download_button("⬇️ Baixar Conjunto de Dados", f.read(), "Conjunto SPED.xml", "application/xml", use_container_width=True)
            else: st.info("XML indisponível.")

    else:
        st.error("Nenhuma conta com movimento detectada.")
else:
    st.info("Aguardando arquivos...")
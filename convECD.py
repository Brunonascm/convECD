import streamlit as st
import pandas as pd
from thefuzz import process, fuzz
import os

st.set_page_config(page_title="DE/PARA SPED ECD", layout="wide")

st.markdown("<style>.cont-row {border-bottom: 1px solid #f0f2f6; padding: 15px 0px;}</style>", unsafe_allow_html=True)

st.title("🛠️ Conversor de Lançamentos ECD")
st.info("Versão 1.0 Beta")

# --- SIDEBAR ---
st.sidebar.header("Configurações")
file_sped = st.sidebar.file_uploader("1. Arquivo SPED (TXT)", type=["txt"])
usar_padrao = st.sidebar.checkbox("Usar Plano de Contas Padrão UNSAO?", value=True)

# FILTRO DE VISUALIZAÇÃO
st.sidebar.divider()
st.sidebar.header("Filtros de Tela")
filtro_status = st.sidebar.selectbox(
    "Mostrar na lista:",
    ["Todas", "Apenas Pendentes", "Apenas Mapeadas"]
)

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
    
    with st.sidebar.expander("ℹ️ Informações de Leiaute"):
        st.markdown("""
        O arquivo Excel deve estar na seguinte ordem **sem cabeçalho**:
        - **Coluna A:** Código Reduzido (o que será gravado)
        - **Coluna B:** Classificação (ex: 1.01.01...)
        - **Coluna C:** Nome da Conta
        """)
        
    if file_excel:
        df_novo = pd.read_excel(file_excel, header=None).iloc[:, [0, 1, 2]]
        df_novo.columns = ['Código', 'Classificação', 'Nome']

def ler_arquivo_texto(file):
    raw_data = file.getvalue()
    content = ""
    for encoding in ["cp1252", "utf-8", "latin-1"]:
        try:
            content = raw_data.decode(encoding)
            break
        except UnicodeError:
            continue
    return [linha.strip() for linha in content.splitlines() if linha.strip()]

# --- Lógica Principal ---
if file_sped and df_novo is not None:
    df_novo = df_novo.astype(str)
    df_novo['Grupo'] = df_novo['Classificação'].str[0]
    df_novo['Display'] = df_novo['Classificação'] + " - " + df_novo['Nome']

    content_sped = ler_arquivo_texto(file_sped)
    
    contas_com_movimento = set()
    for line in content_sped:
        if "|I250|" in line:
            reg = line.split("|")
            if len(reg) > 2:
                contas_com_movimento.add(reg[2].strip())

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
        
        # Inicializa o dicionário de mapeamento no estado da sessão para persistir entre filtros
        if 'de_para_map' not in st.session_state:
            st.session_state.de_para_map = {}

        # Interface de Mapeamento
        for idx, row in df_origem.iterrows():
            cod_atual = row['cod']
            foi_mapeada = cod_atual in st.session_state.de_para_map

            # LÓGICA DO FILTRO DE TELA
            if filtro_status == "Apenas Pendentes" and foi_mapeada:
                continue
            if filtro_status == "Apenas Mapeadas" and not foi_mapeada:
                continue

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
                    res_fuzz = process.extractOne(row['nome'], lista_nomes, scorer=fuzz.token_set_ratio)
                    match_nome, score = res_fuzz[0], res_fuzz[1]
                    
                    opcoes = ["-- SELECIONE --", "📝 -- DIGITAR MANUALMENTE --"] + df_busca['Display'].tolist()
                    
                    # Tenta recuperar o que já foi selecionado para não perder ao filtrar
                    idx_padrao = 0
                    if foi_mapeada:
                        # Se já mapeamos, tentamos achar o índice do valor no display
                        valor_mapeado = st.session_state.de_para_map[cod_atual]
                        # Tenta achar o display que corresponde ao código reduzido mapeado
                        try:
                            display_gravado = df_busca[df_busca['Código'] == valor_mapeado].iloc[0]['Display']
                            idx_padrao = opcoes.index(display_gravado)
                        except:
                            idx_padrao = 1 # Cai no manual se não achar na lista
                    elif score >= 70:
                        sugestao_full = df_busca[df_busca['Nome'] == match_nome].iloc[0]['Display']
                        idx_padrao = opcoes.index(sugestao_full)
                        st.caption(f"✅ Sugestão: {score}%")
                    
                    escolha = st.selectbox(f"sel_{cod_atual}", options=opcoes, index=idx_padrao, key=f"sel_{cod_atual}", label_visibility="collapsed")
                    
                    if escolha == "📝 -- DIGITAR MANUALMENTE --":
                        valor_anterior = st.session_state.de_para_map.get(cod_atual, "")
                        cod_manual = st.text_input(f"Cód. manual para {cod_atual}:", value=valor_anterior, key=f"in_{cod_atual}")
                        if cod_manual: 
                            st.session_state.de_para_map[cod_atual] = str(cod_manual)
                    elif escolha != "-- SELECIONE --":
                        cod_reduzido = df_busca[df_busca['Display'] == escolha].iloc[0]['Código']
                        st.session_state.de_para_map[cod_atual] = str(cod_reduzido)
                    else:
                        # Se voltar para "Selecione", remove do mapa
                        if cod_atual in st.session_state.de_para_map:
                            del st.session_state.de_para_map[cod_atual]
                st.markdown("---")

        # --- RESUMO COM PERCENTUAIS ---
        st.divider()
        total = len(df_origem)
        mapeadas = len(st.session_state.de_para_map)
        pendentes = total - mapeadas
        perc_concluido = (mapeadas / total) * 100 if total > 0 else 0

        col_m1, col_m2, col_m3 = st.columns(3)
        col_m1.metric("Total de Contas", total)
        col_m2.metric("Mapeadas", mapeadas, f"{perc_concluido:.1f}%")
        col_m3.metric("Pendentes", pendentes, f"-{pendentes}", delta_color="inverse")

        if pendentes > 0:
            st.warning(f"⚠️ Existem {pendentes} contas pendentes. Mude o filtro para 'Apenas Pendentes' para agilizar.")
        
        if st.button("🚀 Gerar Novo SPED", disabled=(pendentes > 0), use_container_width=True):
            saida = []
            for line in content_sped:
                if "|I250|" in line:
                    reg = line.split("|")
                    if len(reg) > 2 and reg[2] in st.session_state.de_para_map:
                        reg[2] = st.session_state.de_para_map[reg[2]]
                    saida.append("|".join(reg))
                else:
                    saida.append(line)
            st.success("SPED gerado com sucesso!")
            st.download_button("💾 Baixar Arquivo", "\n".join(saida), "SPED_FINAL.txt", use_container_width=True)
    else:
        st.error("Nenhuma conta com movimento detectada.")
else:
    st.info("Aguardando arquivos...")
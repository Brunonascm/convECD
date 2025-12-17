import streamlit as st
import pandas as pd
from thefuzz import process, fuzz
import os

st.set_page_config(page_title="DE/PARA SPED ECD", layout="wide")

# Estilo para alinhamento
st.markdown("<style>.cont-row {border-bottom: 1px solid #f0f2f6; padding: 15px 0px;}</style>", unsafe_allow_html=True)

st.title("🛠️ Conversor de Lançamentos ECD")

# --- SIDEBAR: Configurações ---
st.sidebar.header("Configurações")
file_sped = st.sidebar.file_uploader("1. Arquivo SPED (TXT)", type=["txt"])

# Opção de Plano Padrão ou Novo Upload
usar_padrao = st.sidebar.checkbox("Usar Plano de Contas Padrão?", value=True)

df_novo = None

if usar_padrao:
    caminho_padrao = "plano_padrao.xlsx" # Nome do arquivo que deve estar na pasta
    if os.path.exists(caminho_padrao):
        try:
            df_novo = pd.read_excel(caminho_padrao, header=None).iloc[:, [0, 1, 2]]
            df_novo.columns = ['Código', 'Classificação', 'Nome']
            st.sidebar.success("✅ Plano padrão carregado!")
        except Exception as e:
            st.sidebar.error(f"Erro ao ler plano padrão: {e}")
    else:
        st.sidebar.warning("Arquivo 'plano_padrao.xlsx' não encontrado no servidor.")
else:
    file_excel = st.sidebar.file_uploader("2. Subir Novo Plano Atual (Excel)", type=["xlsx"])
    if file_excel:
        df_novo = pd.read_excel(file_excel, header=None).iloc[:, [0, 1, 2]]
        df_novo.columns = ['Código', 'Classificação', 'Nome']

# Funções Auxiliares
def ler_arquivo_texto(file):
    raw_data = file.getvalue()
    for encoding in ["cp1252", "utf-8", "latin-1"]:
        try:
            return raw_data.decode(encoding).splitlines()
        except UnicodeError:
            continue
    return []

# --- Lógica Principal ---
if file_sped and df_novo is not None:
    df_novo = df_novo.astype(str)
    df_novo['Grupo'] = df_novo['Classificação'].str[0]
    df_novo['Display'] = df_novo['Classificação'] + " - " + df_novo['Nome']

    content_sped = ler_arquivo_texto(file_sped)
    contas_origem = []
    
    for line in content_sped:
        if line.startswith("|I050|"):
            reg = line.split("|")
            if len(reg) > 8 and len(reg[7]) > 0:
                contas_origem.append({
                    "cod": reg[6], "classif": reg[7], "nome": reg[8], "grupo": reg[7][0]
                })
    
    df_origem = pd.DataFrame(contas_origem).drop_duplicates()

    if not df_origem.empty:
        st.subheader("🔗 Mapeamento de Contas")
        de_para_map = {}

        col_h1, col_h2 = st.columns([1, 1])
        col_h1.markdown("**CONTA NO SPED**")
        col_h2.markdown("**CONTA DESTINO**")
        st.divider()

        for idx, row in df_origem.iterrows():
            with st.container():
                col_origem, col_destino = st.columns([1, 1])
                grupo_atual = row['grupo']
                df_filtrado = df_novo[df_novo['Grupo'] == grupo_atual]
                
                with col_origem:
                    st.markdown(f"**{row['nome']}**")
                    st.caption(f"Cod: {row['cod']} | Classif: {row['classif']}")
                
                with col_destino:
                    if not df_filtrado.empty:
                        lista_nomes_filtrados = df_filtrado['Nome'].tolist()
                        res_fuzz = process.extractOne(row['nome'], lista_nomes_filtrados, scorer=fuzz.token_sort_ratio)
                        
                        match_nome, score = res_fuzz[0], res_fuzz[1]
                        opcoes_grupo = ["-- SELECIONE --"] + df_filtrado['Display'].tolist()
                        
                        idx_padrao = 0
                        if score >= 80:
                            sugestao_full = df_filtrado[df_filtrado['Nome'] == match_nome].iloc[0]['Display']
                            idx_padrao = opcoes_grupo.index(sugestao_full)
                            st.caption(f"✅ Sugestão ({score}%)")
                        else:
                            st.caption(f"⚠️ Similaridade baixa ({score}%)")

                        escolha = st.selectbox(f"sel_{row['cod']}", options=opcoes_grupo, index=idx_padrao, key=f"sel_{row['cod']}", label_visibility="collapsed")
                        
                        if escolha != "-- SELECIONE --":
                            cod_final = df_filtrado[df_filtrado['Display'] == escolha].iloc[0]['Código']
                            de_para_map[row['cod']] = str(cod_final)
                    else:
                        st.error(f"Grupo {grupo_atual} não encontrado.")
                st.markdown("---")

        pendentes = len(df_origem) - len(de_para_map)
        if st.button("🚀 Gerar Novo SPED", disabled=(pendentes > 0), use_container_width=True):
            saida = []
            for line in content_sped:
                if line.startswith("|I250|"):
                    reg = line.split("|")
                    if reg[4] in de_para_map:
                        reg[4] = de_para_map[reg[4]]
                    saida.append("|".join(reg))
                else:
                    saida.append(line)
            st.success("Arquivo processado!")
            st.download_button("💾 Baixar SPED", "\n".join(saida), "SPED_CONVERTIDO.txt", use_container_width=True)
else:
    st.info("Aguardando arquivos para iniciar o processamento.")
import streamlit as st
import pandas as pd
from thefuzz import process, fuzz
import os

st.set_page_config(page_title="DE/PARA SPED ECD", layout="wide")

st.markdown("<style>.cont-row {border-bottom: 1px solid #f0f2f6; padding: 15px 0px;}</style>", unsafe_allow_html=True)

st.title("🛠️ Conversor de Lançamentos ECD")
st.info("Foco: Substituição pelo **Código Reduzido** com indicadores de progresso.")

# --- SIDEBAR ---
st.sidebar.header("Configurações")
file_sped = st.sidebar.file_uploader("1. Arquivo SPED (TXT)", type=["txt"])
usar_padrao = st.sidebar.checkbox("Usar Plano de Contas Padrão UNSAO?", value=True)

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
    
    # PASSO 1: Identificar movimento no I250
    contas_com_movimento = set()
    for line in content_sped:
        if "|I250|" in line:
            reg = line.split("|")
            if len(reg) > 2:
                contas_com_movimento.add(reg[2].strip())

    # PASSO 2: Mapear no I050 de forma dinâmica
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
        de_para_map = {}

        for idx, row in df_origem.iterrows():
            with st.container():
                col_origem, col_destino = st.columns([1, 1])
                grupo_atual = row['grupo']
                df_filtrado = df_novo[df_novo['Grupo'] == grupo_atual]
                df_busca = df_filtrado if not df_filtrado.empty else df_novo
                
                with col_origem:
                    st.markdown(f"**{row['nome']}**")
                    st.caption(f"Cod no SPED: {row['cod']} | Grupo: {grupo_atual}")
                
                with col_destino:
                    lista_nomes = df_busca['Nome'].tolist()
                    res_fuzz = process.extractOne(row['nome'], lista_nomes, scorer=fuzz.token_set_ratio)
                    match_nome, score = res_fuzz[0], res_fuzz[1]
                    
                    opcoes = ["-- SELECIONE --", "📝 -- DIGITAR MANUALMENTE --"] + df_busca['Display'].tolist()
                    idx_padrao = opcoes.index(df_busca[df_busca['Nome'] == match_nome].iloc[0]['Display']) if score >= 70 else 0
                    
                    st.caption(f"✅ Sugestão: {score}%" if score >= 70 else "⚠️ Similaridade baixa")
                    escolha = st.selectbox(f"sel_{row['cod']}", options=opcoes, index=idx_padrao, key=f"sel_{row['cod']}", label_visibility="collapsed")
                    
                    if escolha == "📝 -- DIGITAR MANUALMENTE --":
                        cod_manual = st.text_input(f"Digite o CÓD. REDUZIDO:", key=f"in_{row['cod']}")
                        if cod_manual: de_para_map[row['cod']] = str(cod_manual)
                    elif escolha != "-- SELECIONE --":
                        de_para_map[row['cod']] = df_busca[df_busca['Display'] == escolha].iloc[0]['Código']
                st.markdown("---")

        # --- RESUMO COM PERCENTUAIS ---
        st.divider()
        total = len(df_origem)
        mapeadas = len(de_para_map)
        pendentes = total - mapeadas
        perc_concluido = (mapeadas / total) * 100 if total > 0 else 0

        col_m1, col_m2, col_m3 = st.columns(3)
        col_m1.metric("Total de Contas", total)
        col_m2.metric("Mapeadas", mapeadas, f"{perc_concluido:.1f}%")
        col_m3.metric("Pendentes", pendentes, f"-{pendentes}", delta_color="inverse")

        if pendentes > 0:
            st.warning(f"⚠️ Conclua o mapeamento das {pendentes} contas restantes.")
        
        if st.button("🚀 Gerar Novo SPED", disabled=(pendentes > 0), use_container_width=True):
            saida = []
            for line in content_sped:
                if "|I250|" in line:
                    reg = line.split("|")
                    if len(reg) > 2 and reg[2] in de_para_map:
                        reg[2] = de_para_map[reg[2]]
                    saida.append("|".join(reg))
                else:
                    saida.append(line)
            st.success("SPED gerado com sucesso!")
            st.download_button("💾 Baixar Arquivo", "\n".join(saida), "SPED_FINAL.txt", use_container_width=True)
    else:
        st.error("Nenhuma conta com movimento foi detectada.")
else:
    st.info("Aguardando arquivos...")
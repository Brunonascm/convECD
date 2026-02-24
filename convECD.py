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
    return [linha.strip('\r\n') for linha in content.splitlines() if linha.strip()]

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
    df_novo['Grupo'] = df_novo['Classificação'].str[0]

    content_sped = ler_arquivo_texto_seguro(file_sped)
    
    # --- EXTRAÇÃO DE DADOS DO CABEÇALHO (REGISTRO 0000) ---
    nome_empresa = "EMPRESA"
    dt_inicial_sped = None
    
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
            break
            
    contas_com_movimento = set()
    for line in content_sped:
        if line.startswith("|I250|"):
            reg = line.split("|")
            if len(reg) > 2: contas_com_movimento.add(reg[2].strip())

    contas_origem_data = []
    for line in content_sped:
        if line.startswith("|I050|"):
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
                        if len(reg[j].strip()) > 2 and not reg[j].replace(".","").isnumeric():
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
        
        # --- CÁLCULOS PRINCIPAIS ---
        total_mapeadas_count = 0
        map_final_para_geracao = st.session_state.de_para_map.copy()
        process_data = []

        for idx, row in df_origem.iterrows():
            cod_atual = str(row['cod'])
            grupo_atual = row['grupo']
            
            # --- df_busca: Usado APENAS pela Inteligência Artificial para não confundir o robô ---
            df_filtrado = df_novo[df_novo['Grupo'] == grupo_atual]
            df_busca = df_filtrado if not df_filtrado.empty else df_novo
            
            # --- df_opcoes: Usado no Dropdown (Menu Suspenso) visual ---
            if grupo_atual in ['1', '2']:
                # Se for 1 ou 2, blinda no próprio grupo
                df_opcoes = df_filtrado if not df_filtrado.empty else df_novo
            else:
                # Se for 3, 4, 5, etc., mostra TODAS as contas, EXCETO as do Grupo 1 e 2
                df_opcoes = df_novo[~df_novo['Grupo'].isin(['1', '2'])]
                if df_opcoes.empty: # Segurança caso não existam contas nesses grupos
                    df_opcoes = df_novo 
            
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
                "df_opcoes": df_opcoes,
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
            
            if ocultar_mapeadas and resolvida: continue

            with st.container():
                col_origem, col_destino = st.columns([1, 1])
                with col_origem:
                    st.markdown(f"**{row['nome']}**")
                    st.caption(f"Cod no SPED: {cod_atual} | Grupo: {row['grupo']}")
                
                with col_destino:
                    df_opcoes = item['df_opcoes']
                    opcoes = ["-- SELECIONE --", "📝 -- DIGITAR MANUALMENTE --"] + df_opcoes['Display'].tolist()
                    chave_select = f"sel_{cod_atual}"
                    valor_inicial = opcoes[0]
                    
                    if esta_no_mapa:
                        match_row = df_novo[df_novo['Código'] == item['valor_no_mapa']]
                        if not match_row.empty:
                            display_str = match_row.iloc[0]['Display']
                            if display_str in opcoes:
                                valor_inicial = display_str
                            else:
                                opcoes.insert(2, display_str)
                                valor_inicial = display_str
                        else:
                            valor_inicial = "📝 -- DIGITAR MANUALMENTE --"
                            if f"in_{cod_atual}" not in st.session_state:
                                st.session_state[f"in_{cod_atual}"] = item['valor_no_mapa']
                    elif item['display_sugerido_ia']:
                        if item['display_sugerido_ia'] not in opcoes:
                            opcoes.insert(2, item['display_sugerido_ia'])
                        if chave_select not in st.session_state:
                            valor_inicial = item['display_sugerido_ia']
                        else:
                            valor_inicial = st.session_state[chave_select]

                    if chave_select not in st.session_state:
                        st.session_state[chave_select] = valor_inicial

                    if item['is_manual']: st.info("📌 Mapeado Manualmente")
                    elif item['score'] >= 65: st.success(f"✅ Sugestão: {item['score']}%")
                    else: st.warning(f"⚠️ Similaridade baixa ({item['score']}%)")

                    escolha = st.selectbox(
                        label=f"sel_{cod_atual}", options=opcoes, key=chave_select, label_visibility="collapsed"
                    )

                    novo_valor = None
                    if escolha == "📝 -- DIGITAR MANUALMENTE --": pass
                    elif escolha != "-- SELECIONE --":
                        try:
                            cod_reduzido = escolha.split(" | ")[0]
                            if str(cod_reduzido) != item['valor_no_mapa']: novo_valor = str(cod_reduzido)
                        except: pass
                    elif escolha == "-- SELECIONE --" and esta_no_mapa:
                        del st.session_state.de_para_map[cod_atual]
                        st.rerun()

                    if novo_valor:
                        st.session_state.de_para_map[cod_atual] = novo_valor
                        st.rerun()

                    if escolha == "📝 -- DIGITAR MANUALMENTE --":
                        valor_ant = st.session_state.de_para_map.get(cod_atual, "")
                        st.text_input(f"Cód. manual para {cod_atual}:", value=valor_ant, key=f"in_{cod_atual}", on_change=atualizar_manual, args=(cod_atual,))
                st.markdown("---")

        st.divider()
        col_m1, col_m2, col_m3 = st.columns(3)
        perc_concluido = (total_mapeadas_count / len(df_origem)) * 100 if len(df_origem) > 0 else 0
        col_m1.metric("Total", len(df_origem))
        col_m2.metric("Mapeadas", total_mapeadas_count, f"{perc_concluido:.1f}%")
        col_m3.metric("Pendentes", len(df_origem) - total_mapeadas_count, f"-{len(df_origem) - total_mapeadas_count}", delta_color="inverse")

        # --- FINALIZAÇÃO ---
        st.divider()
        st.subheader("📂 Finalização, Relatórios e Downloads")
        col1, col2, col3, col4 = st.columns(4)

        # 1. SPED AJUSTADO
        sped_buffer = None
        pendentes = len(df_origem) - total_mapeadas_count
        if pendentes == 0:
            saida = []
            for line in content_sped:
                if line.startswith("|9999|"):
                    saida.append(line)
                    break
                if line.startswith("|I250|"):
                    reg = line.split("|")
                    if len(reg) > 2 and reg[2] in map_final_para_geracao:
                        novo_cod = str(map_final_para_geracao[reg[2]]).strip().replace("|", "")
                        reg[2] = novo_cod
                    saida.append("|".join(reg))
                else:
                    saida.append(line)
            sped_buffer = "\r\n".join(saida).encode("latin-1", errors="replace")

        with col1:
            st.markdown("**1. Arquivo Final**")
            if pendentes > 0: 
                st.warning(f"⚠️ Faltam {pendentes}.")
                st.button("🚀 Gerar SPED", disabled=True) 
            else:
                st.download_button(
                    "💾 Baixar SPED Ajustado", data=sped_buffer, 
                    file_name=f"SPED_AJUSTADO_{nome_empresa}.txt", mime="text/plain", use_container_width=True
                )
                
                if os.path.exists("Conjunto SPED.xml"):
                    st.markdown("---")
                    with open("Conjunto SPED.xml", "rb") as f:
                        st.download_button("⬇️ Baixar Conjunto SPED (XML)", f.read(), "Conjunto SPED.xml", "application/xml", use_container_width=True)

        # 2. BALANÇO (I155)
        with col2:
            st.markdown("**2. Balanço (I155)**")
            data_padrao = datetime.today()
            if dt_inicial_sped: data_padrao = dt_inicial_sped - timedelta(days=1)
            data_balanco = st.date_input("Data p/ Balanço:", data_padrao, format="DD/MM/YYYY")
            dt_fmt = data_balanco.strftime("%d/%m/%Y")
            
            if st.button("🔍 Processar Balanço"):
                balanco_lines = ["|6000|V||||"]
                total_debito, total_credito = 0.0, 0.0
                rtl_count = 0
                has_balanco = False
                
                for line in content_sped:
                    if line.startswith("|I150|"): rtl_count += 1
                    if rtl_count == 1 and line.startswith("|I155|"):
                        reg = line.split("|")
                        if len(reg) > 5:
                            cod = reg[2].strip(); val_str = reg[4].strip(); dc = reg[5].strip()
                            try: val_float = float(val_str.replace(",", "."))
                            except: val_float = 0.0
                            if cod in map_final_para_geracao and val_float > 0:
                                novo = map_final_para_geracao[cod].replace("|", "")
                                if dc == 'D': total_debito += val_float
                                else: total_credito += val_float
                                linha = f"|6100|{dt_fmt}|{novo}||{val_str}||SALDO DE ABERTURA EM {dt_fmt}|||||" if dc == 'D' else f"|6100|{dt_fmt}||{novo}|{val_str}||SALDO DE ABERTURA EM {dt_fmt}|||||"
                                balanco_lines.append(linha)
                                has_balanco = True
                
                st.session_state.balanco_dados = "\r\n".join(balanco_lines).encode("latin-1", errors="replace")
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
                        "💾 Baixar Balanço", data=st.session_state.balanco_dados, 
                        file_name=f"BALANCO_{nome_empresa}_{dt_fmt.replace('/','')}.txt", mime="text/plain", use_container_width=True
                    )
                elif pendentes > 0: st.warning("Resolva pendências.")
                else: st.warning("Sem dados.")

        # 3. I157 (Saldos Antigos)
        with col3:
            st.markdown("**3. Troca de Plano (I157)**")
            
            if st.button("🔄 Processar I157"):
                i157_lines = ["ID;;;;;;"]
                rtl_count = 0
                has_i157 = False
                i157_data_list = []
                
                for line in content_sped:
                    if line.startswith("|I150|"): rtl_count += 1
                    
                    if rtl_count == 1 and line.startswith("|I155|"):
                        reg = line.split("|")
                        if len(reg) > 5:
                            cod_antigo = reg[2].strip()
                            val_str = reg[4].strip()
                            dc = reg[5].strip()
                            
                            try:
                                val_float = float(val_str.replace(",", "."))
                            except:
                                val_float = 0.0
                                
                            if cod_antigo in map_final_para_geracao and val_float > 0:
                                novo = map_final_para_geracao[cod_antigo].replace("|", "")
                                i157_data_list.append((novo, cod_antigo, val_str, dc))
                
                if i157_data_list:
                    i157_data_list.sort(key=lambda x: str(x[0]))
                    for item in i157_data_list:
                        novo, cod_antigo, val_str, dc = item
                        
                        if "." in cod_antigo or not cod_antigo.isnumeric():
                            linha = f"C;{novo};;{cod_antigo};{val_str};{dc};"
                        else:
                            linha = f"C;{novo};{cod_antigo};;{val_str};{dc};"
                            
                        i157_lines.append(linha)
                    has_i157 = True
                
                st.session_state.i157_dados = "\r\n".join(i157_lines).encode("latin-1", errors="replace")
                st.session_state.i157_processado = True
                st.session_state.i157_has_data = has_i157
                st.rerun()

            if st.session_state.get('i157_processado'):
                st.markdown("---")
                if st.session_state.i157_has_data and pendentes == 0:
                    st.success("✅ Arquivo I157 gerado!")
                    st.download_button(
                        "💾 Baixar I157", 
                        data=st.session_state.i157_dados, 
                        file_name=f"I157_Saldos_{nome_empresa}.txt", 
                        mime="text/plain", 
                        use_container_width=True
                    )
                    
                    if os.path.exists("Conjunto I157.xml"):
                        st.markdown("---")
                        with open("Conjunto I157.xml", "rb") as f:
                            st.download_button("⬇️ Baixar Conjunto I157 (XML)", f.read(), "Conjunto I157.xml", "application/xml", use_container_width=True)
                            
                elif pendentes > 0:
                    st.warning("Resolva pendências.")
                else:
                    st.warning("Sem dados.")

        # 4. CONFERÊNCIA E CONFIGURAÇÃO
        with col4:
            st.markdown("**4. Conferência**")
            df_pend = df_origem[~df_origem['cod'].isin(map_final_para_geracao.keys())]
            if not df_pend.empty:
                st.warning(f"{len(df_pend)} pendentes.")
                st.download_button("📑 Relatório CSV", df_pend.to_csv(index=False, sep=';', encoding='utf-8-sig'), "contas_pendentes.csv", "text/csv", use_container_width=True)
            else: 
                st.success("✅ Tudo Mapeado OK!")

    else: st.error("Nenhuma conta com movimento detectada.")

if 'de_para_map' in st.session_state and len(st.session_state.de_para_map) > 0:
    with placeholder_botao_salvar:
        st.download_button("⬇️ Salvar Progresso Atual", json.dumps(st.session_state.de_para_map, indent=4), "backup_mapeamento_ecd.json", "application/json", help="Baixe para continuar depois.")
else: st.info("Aguardando arquivos...")
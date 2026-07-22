# Funcionalidades do Obrasys

Este documento registra as features implementadas no sistema e deve ser atualizado a cada nova feature.

## Estoque Core

- Cadastro de materiais com codigo, nome, marca e unidade padrao.
- Cadastro de locais de estoque central e de estoque por obra.
- Controle de saldo por material e local.
- Movimentacoes de entrada, saida, ajustes e transferencia entre locais.
- No admin de movimentacoes, o sistema mostra a quantidade disponivel no local e a quantidade apos a movimentacao.
- Importacao de transferencias por planilha CSV/XLSX entre locais de estoque.
- Conferencia dos itens antes de confirmar a transferencia importada.
- Identificacao de materiais por codigo, descricao normalizada ou alias.
- Validacao de saldo na origem antes e durante a confirmacao.
- Confirmacao atomica da transferencia, usando movimentacao `TRANSFER`.
- Transferencia rapida de um unico material a partir da tela de saldos.

## Estoque em Medicoes

- Fora do fluxo principal da branch MVP.
- A baixa automatica por medicao fica preservada apenas na branch `archive/estoque-features-avancadas`.
- O admin da medicao nao exibe materiais aplicados de estoque na branch MVP.

## Medicoes

- Cadastro individual de linhas contratadas preservado.
- Cadastro em lote de materiais aplicados pela tela da medicao.
- No lote, localizacao, data de aplicacao e notas sao preenchidos uma vez e aplicados a todos os itens.
- Cada item do lote recebe sua propria quantidade e gera um registro separado.
- O lote reaproveita o mesmo fluxo de criacao das linhas contratadas individuais, incluindo historico, consolidacao com linha existente e recalculo de materiais adicionais.
- Itens duplicados no mesmo envio em lote sao bloqueados para evitar soma silenciosa dentro do formulario.

## Carga Inicial de Materiais e Estoque

- Importacao inicial de materiais por CSV e XLSX pelo Django Admin.
- Cadastro de todos os materiais da planilha, mesmo com estoque zero.
- Atualizacao cuidadosa de materiais existentes por codigo ou descricao normalizada.
- Cadastro de categoria, subcategoria, tipo, marca e unidade padrao do material.
- Previa de itens antes da confirmacao, com status e acao prevista.
- Criacao de movimentacoes `ENTRADA_INICIAL` somente para quantidades maiores que zero.
- Atualizacao do saldo do estoque central exclusivamente via movimentacao.
- Bloqueio de confirmacao duplicada do mesmo lote.
- Suporte a linhas ignoradas para cabecalhos duplicados, observacoes ou totais.

## Importacao de Pedidos para Estoque

- Importacao de pedidos de compra por CSV e XLSX pelo Django Admin.
- Conferencia dos itens antes da confirmacao da entrada em estoque.
- Associacao automatica de itens importados a materiais por codigo, descricao normalizada ou alias.
- Cadastro de aliases para reconhecer descricoes de fornecedores.
- Marcacao de itens pendentes quando material, unidade ou quantidade precisam de revisao.
- Registro de marca, fornecedor da linha, valor unitario e valor total quando esses dados vierem na planilha.
- Contadores de linhas, itens validos, itens com pendencia e movimentacoes criadas no lote importado.
- Confirmacao da importacao gerando movimentacoes `PURCHASE_IN`.
- Entrada em estoque central ou estoque de obra, conforme local selecionado.
- Bloqueio de confirmacao duplicada do mesmo pedido importado.
- Confirmacao atomica: se uma linha falhar, nenhuma entrada parcial e mantida.

## Relatorios Simples de Estoque

- Paginas internas protegidas por usuario staff para consulta de estoque.
- Relatorio de saldo por local usando `Stock balances` como fonte.
- Relatorio de saldo por material, com total filtrado por material/local.
- Matriz simples de saldos com materiais nas linhas e locais nas colunas.
- Extrato de movimentacoes usando `Stock movements` como fonte.
- Filtros por local, material, categoria, subcategoria, marca, periodo, usuario e tipo de movimentacao conforme o relatorio.
- Links para abrir material, local, saldo e movimentacao no Django Admin.
- Os relatorios sao somente leitura e nao alteram saldos nem criam movimentacoes.

## Features Avancadas Arquivadas

- Requisicoes de materiais, sugestoes de atendimento e processamento automatico ficam fora da branch MVP.
- Alertas de estoque baixo, falta real por medicao e pedido por alerta ficam fora da branch MVP.
- O codigo historico pode permanecer no banco/modelos para compatibilidade, mas nao aparece no admin principal.
- As features avancadas foram preservadas na branch `archive/estoque-features-avancadas`.

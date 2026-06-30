# Funcionalidades do Obrasys

Este documento registra as features implementadas no sistema e deve ser atualizado a cada nova feature.

## Estoque Core

- Cadastro de materiais com codigo, nome, marca e unidade padrao.
- Cadastro de locais de estoque central e de estoque por obra.
- Controle de saldo por material e local.
- Movimentacoes de entrada, saida, ajustes e transferencia entre locais.
- No admin de movimentacoes, o sistema mostra a quantidade disponivel no local e a quantidade apos a movimentacao.

## Estoque em Medicoes

- Cadastro de materiais aplicados dentro da medicao.
- Unidade do material aplicada automaticamente a partir do cadastro do material.
- Escolha do local de retirada do material aplicado.
- Exibicao da quantidade disponivel no local escolhido.
- Baixa real de estoque ao adicionar material em medicao em rascunho.
- Ajuste automatico do estoque ao alterar quantidade, material ou local do material aplicado.
- Estorno de estoque ao excluir material aplicado, reabrir, rejeitar ou cancelar uma medicao com baixa aplicada.
- Registro de consumo pendente quando a quantidade aplicada supera o saldo disponivel da obra.
- Historico de consumo vinculado a medicao e a movimentacao de estoque.

## Alertas de Estoque e Necessidade de Compra

- Cadastro de regras de estoque minimo por material e local de estoque.
- Geracao automatica de alerta quando o saldo fica abaixo do minimo configurado.
- Geracao automatica de alerta quando uma medicao registra consumo pendente por falta de saldo real na obra.
- Atualizacao de alertas abertos em vez de duplicar alertas para o mesmo material, obra, local e tipo.
- Resolucao automatica de alerta de estoque minimo quando o saldo volta ao minimo configurado.
- Resolucao de alerta de falta real quando o consumo pendente da medicao e zerado ou estornado.
- Visualizacao e controle de alertas pelo Django Admin.
- Acoes no admin para gerar pedido/lista de compra, resolver, ignorar e reabrir alertas.
- Pedido de compra gerado a partir de alertas sem alterar saldo e sem criar movimentacao de estoque.
- Unidade dos itens de compra herdada automaticamente do cadastro do material.

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
- Associacao automatica de itens importados a materiais por descricao normalizada.
- Cadastro de aliases para reconhecer descricoes de fornecedores.
- Marcacao de itens pendentes quando material, unidade ou quantidade precisam de revisao.
- Confirmacao da importacao gerando movimentacoes `PURCHASE_IN`.
- Entrada em estoque central ou estoque de obra, conforme local selecionado.
- Bloqueio de confirmacao duplicada do mesmo pedido importado.

## Requisicao de Materiais com Sugestao de Atendimento

- Criacao de requisicoes de materiais por obra pelo Django Admin.
- Inclusao de materiais solicitados com quantidade na unidade padrao do material.
- Calculo automatico do que pode ser usado do almoxarifado da obra.
- Calculo automatico do que pode ser transferido do estoque central.
- Calculo automatico do que precisa ser comprado.
- Recalculo permitido enquanto a requisicao esta em rascunho ou em analise.
- Aprovacao e cancelamento da requisicao sem movimentar estoque nesta etapa.
- Contadores por requisicao para itens com saldo na obra, transferencia sugerida e compra sugerida.

## Processamento de Requisicao de Materiais

- Processamento de requisicoes aprovadas pelo Django Admin.
- Geracao de transferencia sugerida do estoque central para o almoxarifado da obra.
- Validacao do saldo central atual antes de transferir.
- Atualizacao de saldos via movimentacao `TRANSFER`.
- Geracao de pedido/lista de compra para quantidades sugeridas de compra.
- Vinculo entre item da requisicao e movimentacao de transferencia.
- Vinculo entre item da requisicao e item do pedido de compra.
- Bloqueio de processamento duplicado da mesma requisicao.
- Pedido de compra nao altera estoque; entrada real continua no fluxo de importacao/recebimento.

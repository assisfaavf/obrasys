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

## Estoque em Medicoes

- Fora do fluxo principal da branch MVP.
- A baixa automatica por medicao fica preservada apenas na branch `archive/estoque-features-avancadas`.
- O admin da medicao nao exibe materiais aplicados de estoque na branch MVP.

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

## Features Avancadas Arquivadas

- Requisicoes de materiais, sugestoes de atendimento e processamento automatico ficam fora da branch MVP.
- Alertas de estoque baixo, falta real por medicao e pedido por alerta ficam fora da branch MVP.
- O codigo historico pode permanecer no banco/modelos para compatibilidade, mas nao aparece no admin principal.
- As features avancadas foram preservadas na branch `archive/estoque-features-avancadas`.

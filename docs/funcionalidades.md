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
- Baixa de estoque ao finalizar a medicao.
- Estorno de estoque ao reabrir, rejeitar, cancelar ou excluir uma medicao com baixa aplicada.
- Historico de consumo vinculado a medicao e a movimentacao de estoque.

## Importacao de Pedidos para Estoque

- Importacao de arquivos CSV e XLSX pelo Django Admin.
- Leitura de colunas comuns de pedido: codigo, descricao, unidade, quantidade, valores, fornecedor e observacao.
- Identificacao de materiais por codigo, descricao exata, descricao normalizada e aliases.
- Cadastro de aliases de materiais para equivalencias de fornecedores.
- Conferencia dos itens importados antes de alterar saldo.
- Marcacao de pendencias para material nao identificado, unidade divergente ou quantidade invalida.
- Confirmacao manual da importacao, gerando movimentacoes `PURCHASE_IN`.
- Entrada direta no estoque central ou no almoxarifado de uma obra.
- Bloqueio de confirmacao duplicada para o mesmo lote.

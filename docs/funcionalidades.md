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

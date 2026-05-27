{#
    Override dbt's default schema-naming behavior.

    Default: a model with `+schema: marts` lands in `<target_schema>_marts`
             (e.g., STAGING_marts if target.schema = STAGING). Not what we want.

    With this override: `+schema: marts` lands literally in `marts`.
    A model with NO custom schema falls back to the target schema from profiles.yml.

    Standard pattern — present in basically every production dbt project.
#}
{% macro generate_schema_name(custom_schema_name, node) -%}
    {%- if custom_schema_name is none -%}
        {{ target.schema }}
    {%- else -%}
        {{ custom_schema_name | trim }}
    {%- endif -%}
{%- endmacro %}
